"""Worker Agent — background task executor, runs independently from main agent."""
import asyncio
from typing import AsyncGenerator, Optional

from ..config import AppConfig
from ..logging_config import get_logger

logger = get_logger("agent.worker")


class WorkerTask:
    """A task dispatched to the worker agent."""

    def __init__(self, task_id: str, user_input: str, history: list | None = None):
        self.task_id = task_id
        self.user_input = user_input
        self.history = history or []
        self._done = asyncio.Event()
        self._result: str = ""
        self._cancelled = False

    def set_result(self, text: str):
        self._result = text
        self._done.set()

    def cancel(self):
        self._cancelled = True
        self._done.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def result(self) -> str:
        return self._result

    async def wait(self, timeout: float | None = None):
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


class WorkerAgent:
    """Background agent that executes tasks without blocking the main chat loop.

    Runs OAAAgent in a separate asyncio task, consuming from a task queue.
    Results are sent back via a callback.
    """

    def __init__(self, config: AppConfig):
        from .oaa_agent import OAAAgent

        self.config = config
        self._agent = OAAAgent(config)
        self._queue: asyncio.Queue[WorkerTask] = asyncio.Queue()
        self._current: Optional[WorkerTask] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def submit(self, task_id: str, user_input: str,
                     history: list | None = None) -> WorkerTask:
        """Submit a task and return immediately. Use task.wait() to await result."""
        task = WorkerTask(task_id, user_input, history)
        await self._queue.put(task)
        return task

    async def cancel_current(self):
        """Cancel the currently running task."""
        if self._current and not self._current.cancelled:
            self._current.cancel()
            logger.info("Worker task cancelled: %s", self._current.task_id)

    async def start(self):
        """Start the worker loop."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Stop the worker loop."""
        self._running = False
        if self._current:
            self._current.cancel()
        if self._task:
            self._task.cancel()

    async def _run(self):
        """Main worker loop — consume tasks, execute, report."""
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self._current = task
            logger.info("Worker starting task: %s", task.task_id)

            try:
                result_parts: list[str] = []
                async for chunk in self._agent.process_message(
                    task.user_input, history=task.history
                ):
                    if task.cancelled:
                        break
                    if chunk["type"] == "done":
                        result_parts.append(chunk.get("content", ""))
                    elif chunk["type"] == "llm_output":
                        result_parts.append(chunk.get("content", ""))

                if not task.cancelled:
                    task.set_result("".join(result_parts))
                    logger.info("Worker task completed: %s", task.task_id)
            except Exception as exc:
                logger.error("Worker task failed: %s — %s", task.task_id, exc)
                if not task.cancelled:
                    task.set_result(f"任务执行失败: {exc}")

            self._current = None
            self._queue.task_done()

    async def process_stream(self, task_id: str, user_input: str,
                             history: list | None = None) -> AsyncGenerator[dict, None]:
        """Process a task and yield chunks — used when results need streaming to frontend."""
        task = WorkerTask(task_id, user_input, history)
        await self._queue.put(task)

        while not task.cancelled:
            if task._done.is_set():
                yield {"type": "worker_done", "task_id": task_id, "content": task.result}
                return
            yield {"type": "worker_status", "task_id": task_id,
                   "content": f"任务 {task_id} 排队中..."}
            await asyncio.sleep(1)
