"""Tests for Planner — plan CRUD operations."""
import tempfile
import os
from oaa.agent.planner import Planner


def test_planner_create_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        p = Planner(tmp)
        plan = p.create("Test goal", [
            {"id": 1, "task": "Step 1", "status": "pending"},
            {"id": 2, "task": "Step 2", "status": "pending"},
        ])
        assert plan["goal"] == "Test goal"
        assert plan["status"] == "in_progress"
        assert len(plan["steps"]) == 2

        loaded = p.get(plan["id"])
        assert loaded is not None
        assert loaded["goal"] == "Test goal"


def test_planner_update_step():
    with tempfile.TemporaryDirectory() as tmp:
        p = Planner(tmp)
        plan = p.create("Test", [
            {"id": 1, "task": "Step A", "status": "pending"},
            {"id": 2, "task": "Step B", "status": "pending"},
        ])

        updated = p.update(plan["id"], 1, "done", "Finished step A")
        assert updated["steps"][0]["status"] == "done"
        assert updated["steps"][0]["result"] == "Finished step A"
        assert updated["status"] == "in_progress"  # step 2 still pending

        # Complete all steps → auto-complete plan
        p.update(plan["id"], 2, "done")
        final = p.get(plan["id"])
        assert final is not None
        assert final["status"] == "completed"


def test_planner_update_nonexistent():
    with tempfile.TemporaryDirectory() as tmp:
        p = Planner(tmp)
        result = p.update("nonexistent", 1, "done")
        assert result is None


def test_planner_list_filtered():
    with tempfile.TemporaryDirectory() as tmp:
        p = Planner(tmp)
        p.create("Goal A", [{"id": 1, "task": "X", "status": "done"}])
        plan_b = p.create("Goal B", [{"id": 1, "task": "Y", "status": "pending"}])
        p.update(plan_b["id"], 1, "done")  # auto-completes

        all_plans = p.list_plans()
        assert len(all_plans) == 2

        completed = p.list_plans(status="completed")
        assert len(completed) == 1
