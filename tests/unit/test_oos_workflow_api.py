from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app import oos_workflow_api


def test_prepare_rejects_request_overrides() -> None:
    with pytest.raises(HTTPException) as error:
        oos_workflow_api.prepare_official_oos("protocol-1", {"oos_start": "2021-01-01"})

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "REQUEST_MUST_BE_EMPTY"


def test_execute_requires_prepared_frozen_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oos_workflow_api,
        "_governance",
        lambda protocol_id: (object(), object(), object()),
    )
    monkeypatch.setattr(oos_workflow_api._executions, "get_execution_by_protocol", lambda _: None)

    with pytest.raises(HTTPException) as error:
        oos_workflow_api.execute_official_oos("protocol-1", None)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "OOS_EXECUTION_NOT_PREPARED"
