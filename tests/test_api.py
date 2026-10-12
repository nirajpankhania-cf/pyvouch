"""HTTP surface: the three /reviews endpoints plus health."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vouch import __version__
from vouch.api import app as app_module
from vouch.api.app import app
from vouch.store.runs import RunStore


@pytest.fixture
def api_repo(tmp_path: Path, monkeypatch) -> Path:
    """A real repo with a sloppy uncommitted diff, wired up as the API target."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "initial")
    (tmp_path / "worker.py").write_text("def process():\n    pass\n", encoding="utf-8")

    monkeypatch.setenv(app_module.REPO_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def client(api_repo: Path):
    store = RunStore(":memory:")
    app.dependency_overrides[app_module.get_store] = lambda: store
    with TestClient(app) as test_client:
        test_client.store = store
        yield test_client
    app.dependency_overrides.clear()


class TestHealth:
    def test_reports_ok_and_version(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["version"] == __version__

    def test_reports_the_target_repo(self, client, api_repo: Path):
        assert client.get("/health").json()["repoRoot"] == str(api_repo)

    def test_reports_agent_availability(self, client):
        assert client.get("/health").json()["agentAvailable"] is False

    def test_openapi_is_served(self, client):
        schema = client.get("/openapi.json").json()
        assert "/reviews" in schema["paths"]
        assert "/reviews/{run_id}" in schema["paths"]


class TestServingTheBuiltUi:
    """A built image serves the workspace and the API on one port."""

    def built(self, tmp_path: Path, monkeypatch) -> FastAPI:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>workspace</html>", encoding="utf-8")
        (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
        monkeypatch.setenv(app_module.UI_DIR_ENV, str(dist))

        fresh = FastAPI()
        fresh.get("/health")(lambda: {"status": "ok"})
        assert app_module.mount_ui(fresh) is True
        return fresh

    def test_root_serves_the_bundle(self, tmp_path: Path, monkeypatch):
        with TestClient(self.built(tmp_path, monkeypatch)) as client:
            assert "workspace" in client.get("/").text

    def test_assets_are_served(self, tmp_path: Path, monkeypatch):
        with TestClient(self.built(tmp_path, monkeypatch)) as client:
            assert client.get("/app.js").status_code == 200

    def test_api_routes_still_win(self, tmp_path: Path, monkeypatch):
        with TestClient(self.built(tmp_path, monkeypatch)) as client:
            assert client.get("/health").json() == {"status": "ok"}

    def test_a_missing_asset_is_still_a_404(self, tmp_path: Path, monkeypatch):
        # The workspace is one screen with no client-side routes, so there is
        # deliberately no index.html fallback hiding broken asset paths.
        with TestClient(self.built(tmp_path, monkeypatch)) as client:
            assert client.get("/assets/missing.js").status_code == 404

    def test_nothing_is_mounted_without_a_build(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(app_module.UI_DIR_ENV, str(tmp_path / "never-built"))
        assert app_module.ui_directory() is None
        assert app_module.mount_ui(FastAPI()) is False

    def test_a_directory_without_index_html_is_not_a_build(self, tmp_path: Path, monkeypatch):
        (tmp_path / "dist").mkdir()
        monkeypatch.setenv(app_module.UI_DIR_ENV, str(tmp_path / "dist"))
        assert app_module.ui_directory() is None


class TestOpenApiMatchesSpec:
    """The SPEC's API table is the contract; this test is what keeps it honest."""

    def test_only_the_documented_routes_exist(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert set(paths) == {"/health", "/reviews", "/reviews/{run_id}"}
        assert set(paths["/reviews"]) == {"get", "post"}
        assert set(paths["/reviews/{run_id}"]) == {"get"}

    def test_request_body_fields_are_camel_case(self, client):
        schema = client.get("/openapi.json").json()
        body = schema["components"]["schemas"]["ReviewRequest"]["properties"]
        assert set(body) == {"task", "mode", "baseRef", "noAgent", "model"}
        assert body["mode"]["default"] == "working"

    def test_task_is_the_only_required_field(self, client):
        schema = client.get("/openapi.json").json()
        assert schema["components"]["schemas"]["ReviewRequest"]["required"] == ["task"]

    def test_create_is_documented_as_201(self, client):
        post = client.get("/openapi.json").json()["paths"]["/reviews"]["post"]
        assert "201" in post["responses"]

    def test_list_query_params_are_bounded_as_specified(self, client):
        listing = client.get("/openapi.json").json()["paths"]["/reviews"]["get"]
        params = {param["name"]: param["schema"] for param in listing["parameters"]}
        assert params["limit"]["default"] == 50
        assert (params["limit"]["minimum"], params["limit"]["maximum"]) == (1, 200)
        assert params["offset"]["minimum"] == 0


class TestCreateReview:
    def test_runs_the_pipeline_and_returns_a_report(self, client):
        response = client.post("/reviews", json={"task": "add a worker", "noAgent": True})
        assert response.status_code == 201
        body = response.json()
        assert body["report"]["version"] == 1
        assert body["report"]["task"]["source"] == "api"
        assert body["id"]

    def test_findings_reach_the_response(self, client):
        body = client.post("/reviews", json={"task": "add a worker", "noAgent": True}).json()
        codes = {f["code"] for f in body["report"]["deterministic"]}
        assert "placeholder-code" in codes
        assert body["report"]["verdict"] == "review"

    def test_agent_status_is_explained(self, client):
        body = client.post("/reviews", json={"task": "add a worker"}).json()
        assert body["agentStatus"] == "no-api-key"

    def test_no_agent_is_reported_as_disabled(self, client):
        body = client.post("/reviews", json={"task": "x", "noAgent": True}).json()
        assert body["agentStatus"] == "disabled"

    def test_duration_is_reported(self, client):
        body = client.post("/reviews", json={"task": "x", "noAgent": True}).json()
        assert body["durationMs"] >= 0

    def test_every_review_is_persisted(self, client):
        client.post("/reviews", json={"task": "x", "noAgent": True})
        assert client.store.count() == 1

    def test_staged_mode_is_accepted(self, client):
        response = client.post(
            "/reviews", json={"task": "x", "mode": "staged", "noAgent": True}
        )
        assert response.status_code == 201


class TestCreateReviewValidation:
    def test_empty_task_is_rejected(self, client):
        assert client.post("/reviews", json={"task": ""}).status_code == 422

    def test_missing_task_is_rejected(self, client):
        assert client.post("/reviews", json={}).status_code == 422

    def test_unknown_mode_is_rejected(self, client):
        response = client.post("/reviews", json={"task": "x", "mode": "sideways"})
        assert response.status_code == 422

    def test_base_mode_requires_a_ref(self, client):
        response = client.post("/reviews", json={"task": "x", "mode": "base"})
        assert response.status_code == 422
        assert "baseRef" in response.json()["detail"]

    def test_bad_base_ref_is_a_client_error(self, client):
        response = client.post(
            "/reviews",
            json={"task": "x", "mode": "base", "baseRef": "no-such-ref", "noAgent": True},
        )
        assert response.status_code == 400
        assert "could not resolve" in response.json()["detail"]

    def test_target_that_is_not_a_repo_is_a_client_error(
        self, client, tmp_path_factory, monkeypatch
    ):
        # Not under api_repo: tmp_path itself is the repo this test's client uses.
        outside = tmp_path_factory.mktemp("not-a-repo")
        monkeypatch.setenv(app_module.REPO_ROOT_ENV, str(outside))
        response = client.post("/reviews", json={"task": "x", "noAgent": True})
        assert response.status_code == 400
        assert "not a git repository" in response.json()["detail"]


class TestListReviews:
    def test_empty_history(self, client):
        body = client.get("/reviews").json()
        assert body == {"runs": [], "total": 0}

    def test_newest_first(self, client):
        client.post("/reviews", json={"task": "first task", "noAgent": True})
        client.post("/reviews", json={"task": "second task", "noAgent": True})
        runs = client.get("/reviews").json()["runs"]
        assert [run["taskText"] for run in runs] == ["second task", "first task"]

    def test_summary_fields_only(self, client):
        client.post("/reviews", json={"task": "x", "noAgent": True})
        (run,) = client.get("/reviews").json()["runs"]
        assert set(run) >= {"id", "createdAt", "taskText", "verdict", "findingCount"}
        assert "deterministic" not in run

    def test_total_is_independent_of_the_page(self, client):
        for index in range(3):
            client.post("/reviews", json={"task": f"task {index}", "noAgent": True})
        body = client.get("/reviews", params={"limit": 2}).json()
        assert len(body["runs"]) == 2
        assert body["total"] == 3

    def test_limit_is_bounded(self, client):
        assert client.get("/reviews", params={"limit": 0}).status_code == 422
        assert client.get("/reviews", params={"limit": 9999}).status_code == 422

    def test_negative_offset_is_rejected(self, client):
        assert client.get("/reviews", params={"offset": -1}).status_code == 422


class TestGetReview:
    def test_returns_the_full_report(self, client):
        created = client.post("/reviews", json={"task": "add a worker", "noAgent": True}).json()
        fetched = client.get(f"/reviews/{created['id']}").json()
        assert fetched == created["report"]

    def test_unknown_id_is_404(self, client):
        response = client.get("/reviews/does-not-exist")
        assert response.status_code == 404
        assert "no review with id" in response.json()["detail"]

    def test_reopening_matches_the_original(self, client):
        created = client.post("/reviews", json={"task": "add a worker", "noAgent": True}).json()
        listed = client.get("/reviews").json()["runs"][0]
        reopened = client.get(f"/reviews/{listed['id']}").json()
        assert reopened["verdict"] == created["report"]["verdict"]
        assert listed["verdict"] == created["report"]["verdict"]


class TestNoCheckLogicInTheApi:
    def test_api_and_cli_produce_the_same_report_shape(self, client, api_repo: Path):
        from vouch.context.diff import DiffMode
        from vouch.core.pipeline import PipelineOptions, run_pipeline
        from vouch.core.types import TaskInfo

        via_api = client.post("/reviews", json={"task": "add a worker", "noAgent": True}).json()
        via_core = run_pipeline(
            PipelineOptions(
                mode=DiffMode.working(),
                cwd=str(api_repo),
                task=TaskInfo(text="add a worker", source="api"),
                agent=False,
                prompt=False,
            )
        ).report.to_json_dict()
        assert via_api["report"]["deterministic"] == via_core["deterministic"]
        assert via_api["report"]["verdict"] == via_core["verdict"]
