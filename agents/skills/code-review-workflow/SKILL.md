---
name: code-review-workflow
description: Provides guidance and best practices on Gerrit review coordination, OWNERS/code-ownership and approvals, Python code style/formatting, commit metadata, documentation quality, and developer collaboration workflows in depot_tools.
---

# Depot Tools Code Review Workflow

## Executive Summary

This skill consolidates the review-process, code-ownership, code-style, and
developer-collaboration knowledge for the depot_tools codebase. It defines how
changes move from local development through Gerrit review, OWNERS approval, and
CI feedback, and codifies the Python style, commit metadata, and documentation
standards that every change must satisfy.

## How to use this skill

Use this page as the overview, then open the relevant reference guide below for
the detailed rules, rationale, traps, and worked examples on a given topic. Each
guide is self-contained — read only the one(s) relevant to the change under
review.

## Reference guides

| Topic | When to consult | Guide |
|-------|-----------------|-------|
| Review workflow coordination | Coordinating Gerrit reviews: readiness signaling, reviewer routing, conflict/approval-reset recovery, patchset scope, LUCI config, and CI-feedback resolution. | [Code_Review_Workflow_Coordination_guide.md](references/Code_Review_Workflow_Coordination_guide.md) |
| Code ownership & OWNERS | OWNERS files and approvals: OWNERS config/validation, cross-repository ownership, identity/access enforcement, approval thresholds, delegation, and emergency overrides. | [Code_Ownership_Management_guide.md](references/Code_Ownership_Management_guide.md) |
| Code style & documentation | Python style and docs: language idioms/modernization, static type hints, performance, formatting, commit-message metadata, documentation quality, and regex/data validation. | [Code_Style_and_Documentation_guide.md](references/Code_Style_and_Documentation_guide.md) |
| Developer collaboration | Collaboration practices: external-contributor facilitation, review-thread moderation/signal-to-noise, review-tooling integrations, and environment compatibility. | [Developer_Collaboration_Workflow_guide.md](references/Developer_Collaboration_Workflow_guide.md) |
