---
name: skill-validator
description: Validate skill files specification. Run before committing to ensure SKILL.md files have correct format, required fields, and naming conventions.
argument-hint: "[directory or file path]"
---

# Skill Validator

Validate skill files against the [agentskills.io specification](https://agentskills.io/specification).

## Usage

```
/skill-validator
```

## Instructions

To validate all SKILL.md files under a directory:

```bash
 git clone https://chromium.googlesource.com/chromium/tools/depot_tools.Git
 <directory>
```

To validate specific files:

```bash
agit clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
```

Present the script output to the user. If there are failures, help the user
understand and fix the issues based on the error messages.
