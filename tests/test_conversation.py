from copy import deepcopy

import pytest

from session_vault.chatgpt import VaultError
from session_vault.conversation import parse_conversation

CID = "00000000-0000-4000-8000-000000000001"


def test_current_branch_does_not_mix_regenerations(conversation_data):
    data = conversation_data
    data["mapping"]["m0"]["children"].append("alternate")
    alternate = deepcopy(data["mapping"]["m1"])
    alternate["message"]["content"]["parts"] = ["WRONG BRANCH"]
    data["mapping"]["alternate"] = alternate
    result = parse_conversation(data, CID)
    assert len(result.messages) == 4
    assert "WRONG BRANCH" not in result.markdown()
    assert any("branch" in warning for warning in result.warnings)


@pytest.mark.parametrize("breakage", ["missing_current", "missing_parent", "cycle"])
def test_incomplete_trees_refuse_export(conversation_data, breakage):
    data = conversation_data
    if breakage == "missing_current":
        data["current_node"] = "absent"
    elif breakage == "missing_parent":
        data["mapping"]["m1"]["parent"] = "absent"
    else:
        data["mapping"]["m0"]["parent"] = "m3"
    with pytest.raises(VaultError):
        parse_conversation(data, CID)


def test_hidden_analysis_attachments_and_filename(conversation_data):
    data = conversation_data
    data["title"] = "../../中文\\ unsafe\x00 title"
    data["mapping"]["m1"]["message"]["channel"] = "analysis"
    data["mapping"]["m3"]["message"]["content"]["parts"].append(
        {"content_type": "image_asset_pointer"}
    )
    result = parse_conversation(data, CID)
    assert len(result.messages) == 3
    assert "/" not in result.filename and "\\" not in result.filename
    assert "中文" in result.filename
    assert "Non-text content" in result.messages[-1].text
    assert result.warnings


def test_long_unicode_filenames_fit_filesystem(conversation_data):
    conversation_data["title"] = "中文😀" * 100
    result = parse_conversation(conversation_data, CID)
    assert len((result.filename + ".pdf").encode("utf-8")) < 255
