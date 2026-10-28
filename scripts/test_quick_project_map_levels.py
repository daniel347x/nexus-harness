"""Check the shared quick doctrine and its generated Manager agent stay aligned."""

from pathlib import Path

import build_manager_quick_agent as generator


def section_three(text: str) -> str:
    return text.split("## 3 · Before any code, the documentation", 1)[1].split(
        "## 4 · Finding code", 1
    )[0]


def test_quick_workers_and_helpers_share_all_four_level_definitions():
    text = generator.SOURCE.read_text(encoding="utf-8")
    section = section_three(text)
    for definition in (
        "**1** = render the map",
        "**2** = relevant required entries and documentation",
        "**3** = the full Slice Catalog",
        "**4** = beacon slices and source reconnaissance",
    ):
        assert definition in section
    assert "Apply reading assignments independently to each map" in section
    assert "a shared catalog need not be read twice" in section
    assert "give its reading levels (§3)" in text


def test_explicit_levels_replace_discovery_defaults_without_waiving_safety():
    section = section_three(generator.SOURCE.read_text(encoding="utf-8"))
    for rule in (
        "Level 1 is always read",
        "Listed levels are assigned; unlisted levels are available when needed",
        "free to go to a deeper reading level as you see fit, but are not required to",
        "replace the default automatic map-selection pass below",
        "not explicit task reads, standing rules or action-specific must-reads",
        "required resource-reading list belongs to level 2",
        "**Without level assignments, use the default pass:**",
        "Before changing code, read any agent README",
        "a reading level does not waive those safeguards",
    ):
        assert rule in section


def test_first_message_maps_and_configured_location_fallback_share_the_render_instruction():
    section = section_three(generator.SOURCE.read_text(encoding="utf-8"))
    for rule in (
        "If your first message or prompt hands you a **Project Map**",
        "render it in full before you act",
        "render each in full before you act",
        "system(verb='config.get', params={})",
        "`project_maps_location`",
        "No location, no map for it, or no tab to read it: carry on without one",
        "A quick helper does this only when its task needs orientation",
    ):
        assert rule in section


def test_committed_manager_agent_contains_the_exact_shared_doctrine():
    source = generator.SOURCE.read_text(encoding="utf-8")
    assert generator.TARGET.read_text(encoding="utf-8") == (
        generator.HEADER + generator.PREAMBLE + source
    )


def test_generation_is_verbatim_and_idempotent(tmp_path: Path, monkeypatch):
    source_text = generator.SOURCE.read_text(encoding="utf-8")
    source = tmp_path / "AGENTS.md"
    target = tmp_path / "manager-quick.md"
    source.write_text(source_text, encoding="utf-8")
    monkeypatch.setattr(generator, "SOURCE", source)
    monkeypatch.setattr(generator, "TARGET", target)
    assert generator.build() is True
    assert target.read_text(encoding="utf-8") == (
        generator.HEADER + generator.PREAMBLE + source_text
    )
    assert generator.build() is False
