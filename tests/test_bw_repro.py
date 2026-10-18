import json
import json as jsonlib
import pytest
from songhive.federation.fetch import FetchResult
from songhive.services import remote_content as rc

pytestmark = pytest.mark.anyio


@pytest.fixture
def remote_config(config, tmp_path):
    config.federation.instance_domain = "local.invalid"
    config.federation.private_key_path = tmp_path / "actor.pem"
    return config


def _doc_result(url, doc):
    return FetchResult(
        url=url, status_code=200, content_type="application/activity+json", body=jsonlib.dumps(doc).encode(), headers={}
    )


class FakeFetcher:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *, check_url=None, **kwargs):
        self.calls.append(url)
        if url in self.routes:
            return _doc_result(url, self.routes[url])
        raise AssertionError(f"Unstubbed fetch: {url}")


async def test_album2(db_session, remote_config, monkeypatch):
    doc = json.load(open("/tmp/bw_album2.json"))
    children = json.load(open("/tmp/bw_children2.json"))
    actor = json.load(open("/tmp/bw_actor3.json"))
    fake = FakeFetcher({doc["id"]: doc, children["id"]: children, actor["id"]: actor})
    monkeypatch.setattr(rc, "guarded_fetch", fake)
    result = await rc.dereference_remote_object(db_session, remote_config, doc["id"])
    row = result.remote_object
    print("\nobject_type:", row.object_type, "| resource_type:", row.resource_type)
    children_rows = await rc.get_remote_object_children(db_session, row)
    print("children:", [(c.name, c.object_type) for c in children_rows])
    assert len(children_rows) == 12
