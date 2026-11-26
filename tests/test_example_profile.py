"""저장소의 profile.example.toml이 실제 스키마와 맞는지 (양식을 바꾸면 예시도 같이 고치도록)."""

import sys
from pathlib import Path

from sh_notice_mcp import profile as pf
from sh_notice_mcp.config import watched_boards

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

EXAMPLE = Path(__file__).parent.parent / "profile.example.toml"


def test_example_profile_is_valid():
    data = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    for sec, keys in data.items():
        for key, value in keys.items():
            pf.validate_change(f"{sec}.{key}", value)
    assert [b.id for b in watched_boards(data)] == ["sale", "rent_jeonse", "rent_happy"]
