"""R2.1 Task Intake CLI tests."""

from __future__ import annotations

import json

from runtime.mvp_runtime import cli


def test_cli_argv_happy_path_emits_valid_task(capsys):
    rc = cli.main(["이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    task = json.loads(out)
    assert task["schema_version"] == "task.v0.3"
    assert task["lifecycle"]["status"] == "RECEIVED"
    # Non-ASCII request round-trips losslessly through the CLI.
    assert "사업 아이디어" in task["request"]["raw_request"]


def test_cli_empty_argv_is_usage_block(capsys):
    rc = cli.main([""])
    assert rc == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "EMPTY_REQUEST" in err


def test_cli_bom_only_argv_is_usage_block(capsys):
    rc = cli.main(["﻿"])
    assert rc == cli.EXIT_USAGE
    assert "EMPTY_REQUEST" in capsys.readouterr().err
