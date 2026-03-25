---
name: skill-validator
description: Validate skill files specification. Run before committing to ensure SKILL.md files have correct format, required fields, and naming conventions.
---

# Skill Validator

Validate skill files in this repository against the [agentskills.io specification](https://agentskills.io/specification).

## Usage

```
/skill-validator
```

## Instructions

When invoked, run the validation script to check all skills in the
`agents/skills/` directory:

```bash
agents/skills/skill-validator/scripts/skill_validator.py --all
```

To validate specific files only:

```bash
agents/skills/skill-validator/scripts/skill_validator.py path/to/SKILL.md
```

Present the script output to the user. If there are failures, help the user
understand and fix the issues based on the error messages.
