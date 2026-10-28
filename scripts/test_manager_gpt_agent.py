#!/usr/bin/env python3
"""Offline checks for the generated GPT Manager agent (agents/agent/manager-gpt.md).

    python3 scripts/test_manager_gpt_agent.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_manager_gpt_agent as B  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class ManagerGptAgent(unittest.TestCase):
    def setUp(self):
        self.text = B._read(B.TARGET)

    def test_generated_file_is_current(self):
        self.assertEqual(self.text, B.render(),
                         'manager-gpt.md is stale: run python3 scripts/build_manager_gpt_agent.py and commit it')

    def test_overlay_sits_at_start_hinge_and_end_around_the_whole_shared_prompt(self):
        _, body = B.split_frontmatter(self.text)
        opening = B._read(B.OVERLAY / 'opening.md').strip('\n')
        bridge = B._read(B.OVERLAY / 'bridge.md').strip('\n')
        closing = B._read(B.OVERLAY / 'closing.md').strip('\n')
        self.assertTrue(body.startswith(opening), 'the GPT opening must be the very first text of the prompt')
        self.assertTrue(body.rstrip('\n').endswith(closing), 'the closing check must be the very last text')
        self.assertIn(bridge + '\n\n' + B.BRIDGE_ANCHOR, body, 'the bridge must sit just before the tool reference')
        # The proxy recognises a Manager request by this marker (1-hour cache TTL on Anthropic routes).
        self.assertIn('# CONVERSATION CONTINUITY AGENT', body)
        _, shared = B.split_frontmatter(B._read(B.SOURCE))
        for heading in [l for l in shared.split('\n') if l.startswith('# ')]:
            self.assertIn(heading, body)

    @unittest.skipIf(yaml is None, 'PyYAML not installed')
    def test_frontmatter_is_a_gpt_primary_agent_with_the_managers_permissions(self):
        mine = yaml.safe_load(B.split_frontmatter(self.text)[0])
        shared = yaml.safe_load(B.split_frontmatter(B._read(B.SOURCE))[0])
        self.assertEqual(mine['mode'], 'primary')
        self.assertTrue(mine['model'].startswith('openai/gpt-'))
        self.assertIsInstance(mine['description'], str)
        self.assertEqual(mine.get('permissions'), shared.get('permissions'))


if __name__ == '__main__':
    unittest.main()
