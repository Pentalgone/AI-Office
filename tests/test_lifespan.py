import asyncio

import app.main as main_module
import app.services.task_worker as worker_module


def test_lifespan_starts_and_stops_background_worker(monkeypatch):
    monkeypatch.setattr(main_module, "initialize_database", lambda: None)
    started = asyncio.Event()

    async def fake_worker(execute_task, stop_event):
        assert execute_task is main_module.execute_task_workflow
        started.set()
        await stop_event.wait()

    monkeypatch.setattr(worker_module, "task_worker_loop", fake_worker)

    async def run_lifespan():
        async with main_module.lifespan(main_module.app):
            await asyncio.sleep(0)
            assert started.is_set()
            assert not main_module.app.state.task_worker_stop.is_set()

        assert main_module.app.state.task_worker_stop.is_set()

    asyncio.run(run_lifespan())
