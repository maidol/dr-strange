"""The binding's own tests: run against a built wheel, never the source tree.

Each one opens an in-memory database, so nothing here touches the filesystem
and the tests are order-independent — which matters more than usual, because a
plane is process-global state inside one `Database` and a leaked name would
make a later test pass for the wrong reason.
"""

from __future__ import annotations

import asyncio

import pytest

import drsg_embedded as drsg

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    """asyncio only: the binding's runtime is tokio, and trio adds nothing to
    what these tests check."""
    return "asyncio"


async def seeded():
    """Two symbols and the call between them, in a plane named `p`."""
    db = await drsg.open_memory()
    await db.create_plane("p")
    await db.write_nodes(
        "p",
        [
            {
                "key": "Service.Save",
                "labels": ["Method"],
                "properties": {"file": "service.go", "line": 12},
            },
            {
                "key": "normalize",
                "labels": ["Function"],
                "properties": {"file": "service.go", "line": 21},
            },
        ],
    )
    await db.write_edges("p", [{"src": "Service.Save", "dst": "normalize", "type": "CALLS"}])
    return db


async def test_version_is_the_engines():
    assert drsg.__version__.count(".") == 2


async def test_writes_report_what_they_wrote():
    db = await drsg.open_memory()
    await db.create_plane("p")
    assert await db.write_nodes("p", [{"key": "a"}, {"key": "b"}]) == 2
    assert await db.write_edges("p", [{"src": "a", "dst": "b", "type": "CALLS"}]) == 1


async def test_a_node_comes_back_as_python_data():
    db = await seeded()
    node = await db.get_node("p", "Service.Save")
    # Dicts and lists, not a JSON string: the conversion happens when the
    # future resolves, which is the whole point of the Json newtype.
    assert isinstance(node, dict)
    assert node["labels"] == ["Method"]
    assert node["properties"]["line"] == 12
    assert await db.get_node("p", "absent") is None


async def test_planes_counts_what_each_holds():
    db = await seeded()
    planes = {p["name"]: p for p in await db.planes()}
    assert planes["p"]["nodes"] == 2
    assert planes["p"]["edges"] == 1


async def test_context_names_the_caller_and_its_own_bound():
    db = await seeded()
    answer = await db.context("p", "normalize")
    assert "Service.Save" in answer
    # Every answer states what it walked rather than implying completeness.
    assert "lower bound" in answer


async def test_impact_walks_backwards_by_distance():
    db = await seeded()
    answer = await db.impact("p", "normalize", depth=3)
    assert "depth 1" in answer
    assert "Service.Save" in answer


async def test_trace_finds_the_recorded_path():
    db = await seeded()
    assert "normalize" in await db.trace("p", "Service.Save", "normalize")


async def test_describe_is_the_node_without_its_edges():
    db = await seeded()
    answer = await db.describe("p", "normalize")
    assert "service.go" in answer
    assert "callers" not in answer


async def test_a_missing_plane_raises_rather_than_answering_empty():
    db = await drsg.open_memory()
    with pytest.raises(RuntimeError, match="nosuch"):
        await db.context("nosuch", "x")


async def test_a_node_without_a_key_is_rejected_by_name():
    db = await drsg.open_memory()
    await db.create_plane("p")
    with pytest.raises(ValueError, match="`key`"):
        await db.write_nodes("p", [{"labels": ["X"]}])


async def test_an_edge_onto_a_missing_endpoint_is_rejected():
    db = await drsg.open_memory()
    await db.create_plane("p")
    await db.write_nodes("p", [{"key": "a"}])
    with pytest.raises(ValueError, match="keyed"):
        await db.write_edges("p", [{"src": "a", "dst": "ghost", "type": "CALLS"}])


async def test_the_loop_stays_free_while_the_engine_works():
    """Concurrent calls are what `spawn_blocking` buys; without it the first
    await would hold the loop for the whole engine call."""
    db = await seeded()
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(tick())
    answers = await asyncio.gather(*(db.describe("p", "normalize") for _ in range(16)))
    ticker.cancel()
    assert len(set(answers)) == 1
    assert ticks > 0, "the event loop never ran while the engine did"


async def test_a_file_backed_database_persists_across_opens(tmp_path):
    path = str(tmp_path / "graph.drsg")
    db = await drsg.open(path)
    await db.create_plane("p")
    await db.write_nodes("p", [{"key": "a", "labels": ["N"]}])
    del db

    again = await drsg.open(path)
    assert (await again.get_node("p", "a"))["labels"] == ["N"]
