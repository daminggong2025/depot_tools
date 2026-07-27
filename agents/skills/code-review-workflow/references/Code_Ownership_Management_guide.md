# Code Ownership Management Engineering Guide

## Executive Summary

Welcome to the Code Ownership Management Engineering Guide. This repository
serves as the authoritative source of tribal knowledge and operational standards
governing code review, identity verification, and repository access control. It
was created to codify historically unwritten rules, establish clear
architectural boundaries for repository access, and proactively prevent the
regression of known failure modes that stall Continuous Integration (CI)
pipelines. By centralizing these directives, we ensure that the codebase remains
securely governed, auditable, and resilient to bottlenecks caused by
organizational churn or tooling limitations.

This guide encompasses a wide spectrum of operational domains essential to
maintaining high-velocity development without compromising security or
structural integrity. Core topics include the syntactic configuration and
validation of `OWNERS` files, the enforcement of Single Sign-On (SSO) identity
verification, and mechanisms for centralizing cross-repository access.
Furthermore, it explicitly defines the rules of engagement for multi-tiered
approvals, domain deferrals to Subject Matter Experts (SMEs), and the precise,
safe invocation of emergency bypasses to unblock critical infrastructure
failures.

Ultimately, these mandates protect the integrity of the review state machine and
streamline the development lifecycle. By standardizing administrative
workflows—such as facilitating external contributors, demarcating patchset
history, and navigating Gerrit automation quirks—this guide equips engineers
with the context needed to safely maneuver complex access control thresholds.
Incoming engineers and maintainers are expected to consult these chapters to
guarantee secure, efficient, and policy-compliant code submissions.

## Summary

| Chapter Theme / Title                | Scope & Objective                     |
| :----------------------------------- | :------------------------------------ |
| **OWNERS File Configuration &        | This domain enforces strict           |
: Validation**                         : conventions for OWNERS files,         :
:                                      : governing explicit per-file           :
:                                      : directives, redundant maintainer      :
:                                      : assignments, and email domain         :
:                                      : resolution. Adhering to these         :
:                                      : structural and validation constraints :
:                                      : ensures seamless Gerrit code-owners   :
:                                      : parsing and prevents review pipeline  :
:                                      : bottlenecks.                          :
| **Cross-Repository Ownership         | Centralize cross-repository access    |
: Centralization**                     : control using Gerrit `include`        :
:                                      : directives to establish a single      :
:                                      : source of truth for code ownership.   :
:                                      : This prevents configuration drift and :
:                                      : eliminates manual synchronization     :
:                                      : overhead across distributed           :
:                                      : repositories.                         :
| **Administrative Overrides &         | This chapter governs the mechanisms   |
: Emergency Bypasses**                 : and justifications for bypassing      :
:                                      : standard OWNERS review gates, such as :
:                                      : `owners-override` and `OO+1`. These   :
:                                      : overrides must be strictly reserved   :
:                                      : for resolving critical infrastructure :
:                                      : failures, unblocking builds, or       :
:                                      : restoring review states when primary  :
:                                      : owners are offline or tooling fails.  :
| **Identity Verification & Access     | Enforce strict reviewer identity      |
: Control Enforcement**                : verification and role-based committer :
:                                      : access within Gerrit workflows to     :
:                                      : prevent blocked continuous            :
:                                      : integration pipelines. Unlinked       :
:                                      : corporate accounts and legacy group   :
:                                      : aliases must be explicitly mapped to  :
:                                      : valid SSO identities to satisfy       :
:                                      : mandatory repository submit rules.    :
| **Multi-Tiered Approval Thresholds** | This chapter dictates the enforcement |
:                                      : of numerical and domain-specific      :
:                                      : approval thresholds mandated by       :
:                                      : repository governance. It defines the :
:                                      : protocols for satisfying multi-tier   :
:                                      : sign-offs, such as acquiring          :
:                                      : secondary committer votes, explicit   :
:                                      : domain team approvals, and verified   :
:                                      : employee stamps, to successfully      :
:                                      : unblock CI pipelines.                 :
| **Review Delegation & Domain         | This domain governs the explicit      |
: Deferral**                           : transition of review                  :
:                                      : responsibilities, mandating that      :
:                                      : reviewers lacking domain expertise or :
:                                      : committer rights formally defer to    :
:                                      : Subject Matter Experts (SMEs) or move :
:                                      : to the CC list. It enforces           :
:                                      : conditional approval mechanisms (LGTM :
:                                      : %) and explicit non-blocking          :
:                                      : declarations to maintain pipeline     :
:                                      : velocity without compromising         :
:                                      : technical rigor.                      :
| **Review Tooling Automation Quirks** | This chapter governs the mitigation   |
:                                      : of platform side-effects and          :
:                                      : automation bugs within the Gerrit     :
:                                      : code review ecosystem. It dictates    :
:                                      : how to navigate UI limitations, stuck :
:                                      : submit rules, and interrupted         :
:                                      : auto-submit queues to maintain CI     :
:                                      : pipeline velocity.                    :
| **Review Pipeline State &            | This chapter governs the lifecycle    |
: Meta-Coordination**                  : management of changelists (CLs),      :
:                                      : dictating strict protocols for        :
:                                      : patchset history demarcation,         :
:                                      : retrospective reviews, and            :
:                                      : auto-submit state management.         :
:                                      : Adherence ensures CI pipeline         :
:                                      : velocity without compromising         :
:                                      : auditability or reviewer context.     :
| **External Contributor               | Core maintainers must actively proxy  |
: Facilitation**                       : Continuous Integration (CI)           :
:                                      : execution, validate Contributor       :
:                                      : License Agreements (CLAs), and        :
:                                      : manually merge changelists on behalf  :
:                                      : of external or unpermissioned         :
:                                      : authors. This administrative          :
:                                      : facilitation prevents approved        :
:                                      : open-source contributions from        :
:                                      : stalling indefinitely due to internal :
:                                      : access restrictions.                  :

--------------------------------------------------------------------------------
--------------------------------------------------------------------------------

## Chapter: OWNERS File Configuration & Validation

**Context:** This domain enforces strict conventions for OWNERS files, governing
explicit per-file directives, redundant maintainer assignments, and email domain
resolution. Adhering to these structural and validation constraints ensures
seamless Gerrit code-owners parsing and prevents review pipeline bottlenecks.

### Summary

| Rule ID   | Principle / Constraint   | Priority | Primary Symptom / Trap    |
| :-------- | :----------------------- | :------- | :------------------------ |
| **T1-01** | Explicit Ownership       | High     | Adding a new toolchain or |
:           : Assignment for           :          : configuration file to the :
:           : Configuration Files      :          : repository without        :
:           :                          :          : specifying who is         :
:           :                          :          : responsible for its       :
:           :                          :          : ongoing maintenance.      :
| **T1-02** | OWNERS File Email Domain | High     | Adding an internal or     |
:           : Standardization and      :          : mistyped email address    :
:           : Validation               :          : that does not match the   :
:           :                          :          : public Gerrit account     :
:           :                          :          : identity.                 :
| **T1-03** | Preservation of Group    | High     | Blindly deleting lines    |
:           : Directives in OWNERS     :          : near a departing user's   :
:           : Files                    :          : email, catching broad     :
:           :                          :          : group ownership files in  :
:           :                          :          : the process.              :
| **T1-04** | Minimum Redundancy Rule  | Medium   | Leaving an OWNERS file    |
:           : for Directory Owners     :          : with only a single        :
:           :                          :          : maintainer after a        :
:           :                          :          : colleague departs.        :
| **T1-05** | Secondary Email False    | Low      | Treating vague Gerrit     |
:           : Positives in Gerrit      :          : code-owners resolution    :
:           : Code-Owners              :          : warnings as hard failures :
:           :                          :          : during repository         :
:           :                          :          : configuration updates.    :
| **T1-06** | Proactive Stakeholder    | Medium   | Bootstrapping a new       |
:           : Inclusion in Library     :          : library with only the     :
:           : OWNERS Files             :          : author's email in the     :
:           :                          :          : OWNERS file.              :

--------------------------------------------------------------------------------

### Rules

#### T1-01: Explicit Ownership Assignment for Configuration Files

> **Rule:** Must explicitly assign per-file ownership in adjacent OWNERS files
> when introducing new top-level configuration files to ensure proper review
> routing.
>
> **What:** Newly introduced top-level configuration files (such as linters)
> must have explicit per-file ownership defined in the adjacent OWNERS file to
> ensure future changes are properly routed.
>
> **Applies To:** Top-level repository configuration, toolchain settings files
> (e.g., .ruff.toml), and OWNERS files.
>
> **Why:** New configuration files were merged without designated maintainers.
> Future modifications to these configurations lacked clear routing, resulting
> in ambiguity over who should review linter or tooling changes. Failing to
> adhere to this typically results in **Orphaned Configuration**.

**Trap 1: Adding a new toolchain or configuration file to the repository without
specifying who is responsible for its ongoing maintenance.**

**Don't:**

*   Merging a new `.ruff.toml` file without touching the `OWNERS` file.

**Do:**

*   Update the adjacent `OWNERS` file with a `per-file .ruff.toml=*@domain.com`
    directive to designate the specific subject matter expert.

--------------------------------------------------------------------------------

#### T1-02: OWNERS File Email Domain Standardization and Validation

> **Rule:** Must use externally resolvable email domains (e.g., `@chromium.org`)
> matching the maintainer's registered Gerrit review account in public
> repository OWNERS files.
>
> **What:** OWNERS files in public repositories must use externally resolvable
> email domains (e.g., @chromium.org) that match the maintainer's registered
> Gerrit review account to prevent validation failures.
>
> **Applies To:** Gerrit OWNERS configuration files, specifically within public
> repositories like depot_tools.
>
> **Why:** Historically, using internal corporate domains or introducing typos
> in the TLD (e.g., .com instead of .org) caused the code-owners plugin to fail
> resolution, generating invalid code owner config warnings and blocking
> ownership enforcement. Failing to adhere to this typically results in
> **Validation Warning / Unresolvable Owner**.

**Trap 1: Adding an internal or mistyped email address that does not match the
public Gerrit account identity.**

**Don't:**

```text
# BAD: Using .com typo or internal domain for a public repo owner
user1@chromium.com
user2@google.com
```

**Do:**

```text
# GOOD: Using the exact registered external domain
user1@chromium.org
user2@chromium.org
```

**Exceptions:** When a maintainer explicitly only uses an internal account for
code reviews and lacks an external organization account.

--------------------------------------------------------------------------------

#### T1-03: Preservation of Group Directives in OWNERS Files

> **Rule:** Never accidentally delete overarching group references or cross-repo
> includes when pruning individual users from an OWNERS file.
>
> **What:** When removing an individual user from an OWNERS file, overlapping
> group references or cross-repo includes (e.g., `file://BUILD_OWNERS`) must be
> strictly preserved.
>
> **Applies To:** OWNERS file maintenance, offboarding procedures, and directory
> permission updates.
>
> **Why:** During offboarding updates, engineers accidentally deleted adjacent
> lines containing structural group ownership directives, inadvertently locking
> out legitimate administrative teams from the directory. Failing to adhere to
> this typically results in **Permission Lockout / Loss of Coverage**.

**Trap 1: Blindly deleting lines near a departing user's email, catching broad
group ownership files in the process.**

**Don't:**

```text
# Deleting both the individual and the fallback group
- per-file post_build_ninja_summary.py=[User]@chromium.org
- per-file post_build_ninja_summary.py=file://BUILD_OWNERS
```

**Do:**

```text
# Only removing the specific departing user
- per-file post_build_ninja_summary.py=[User]@chromium.org
  per-file post_build_ninja_summary.py=file://BUILD_OWNERS
```

--------------------------------------------------------------------------------

#### T1-04: Minimum Redundancy Rule for Directory Owners

> **Rule:** Always designate a minimum of two individuals or broad functional
> groups per OWNERS file to prevent single points of failure in the review
> pipeline.
>
> **What:** Every OWNERS file must designate at least two individuals or broad
> functional groups to prevent a single point of failure in the review pipeline.
>
> **Applies To:** All local directory OWNERS files across the codebase.
>
> **Why:** Directories with a single owner would become completely blocked from
> modifications if that owner went on leave, offboarded, or became unresponsive.
> Failing to adhere to this typically results in **Blocked Review Pipeline (Bus
> Factor 1)**.

**Trap 1: Leaving an OWNERS file with only a single maintainer after a colleague
departs.**

**Don't:**

```text
# BAD: Only one person can approve changes
[UserA]@chromium.org
```

**Do:**

```text
# GOOD: Redundant ownership ensures availability
[UserA]@chromium.org
[UserB]@chromium.org
```

**Exceptions:** Deeply specialized, deprecated, or experimental subdirectories
where only one engineer has functional context.

--------------------------------------------------------------------------------

#### T1-05: Secondary Email False Positives in Gerrit Code-Owners

> **Rule:** Bypass and proceed past benign `cannot be resolved` Gerrit
> code-owners warnings when they are triggered by valid, non-visible secondary
> emails.
>
> **What:** Gerrit `[code-owners]` validation warnings during push (e.g.,
> 'cannot be resolved') are often benign false positives triggered by
> non-visible secondary emails and should not block submissions if the email is
> valid.
>
> **Applies To:** Gerrit pre-receive hooks and code-owners plugin validation
> checks.
>
> **Why:** Engineers wasted significant time trying to debug 'invalid code owner
> config files' warnings during push operations, which were actually just
> undocumented plugin visibility quirks regarding secondary accounts. Failing to
> adhere to this typically results in **Wasted Debugging Time / Stalled Push**.

**Trap 1: Treating vague Gerrit code-owners resolution warnings as hard failures
during repository configuration updates.**

**Don't:**

*   Halting an OWNERS update to investigate why a valid colleague's email
    triggered a 'cannot be resolved' remote warning.

**Do:**

*   Acknowledging the warning as a known plugin limitation regarding secondary
    emails and proceeding with the submission.

**Exceptions:** If the email domain is explicitly restricted or misspelled
(e.g., a typo in `@chromium.org`).

--------------------------------------------------------------------------------

#### T1-06: Proactive Stakeholder Inclusion in Library OWNERS Files

> **Rule:** Must proactively include expected cross-functional stakeholders
> alongside the primary author when creating a new library's OWNERS file.
>
> **What:** When introducing a new library or component, the OWNERS file must be
> proactively populated with expected users and cross-functional stakeholders to
> establish a shared ownership model.
>
> **Applies To:** Creation of new directories, libraries, or modules (e.g.,
> configuring `libs/telemetry/OWNERS`).
>
> **Why:** Newly created libraries with single-author OWNERS files frequently
> become maintenance bottlenecks when the original author is unavailable or
> moves to another project. Failing to adhere to this typically results in
> **Ownership Bottleneck / Stale Code**.

**Trap 1: Bootstrapping a new library with only the author's email in the OWNERS
file.**

**Don't:**

```text
# OWNERS
author@google.com
```

**Do:**

```text
# OWNERS
author@google.com
stakeholder1@google.com
stakeholder2@chromium.org
```

**Exceptions:** Highly experimental or tightly scoped personal sandbox
directories.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T4 | Identity Verification & Access Control Enforcement -
    *Gerrit account verification and linked account requirements directly
    dictate which email domains resolve successfully within OWNERS files.*
*   **Downstream:** T2 | Cross-Repository Ownership Centralization - *Properly
    formatted include structures (`file://BUILD_OWNERS`) scale to become the
    source of truth for cross-repo governance.*
*   **Downstream:** T7 | Review Tooling Automation Quirks - *Validation false
    positives in OWNERS configurations surface Gerrit plugin bugs that must be
    managed as tooling quirks.*

## Chapter: Cross-Repository Ownership Centralization

**Context:** Centralize cross-repository access control using Gerrit `include`
directives to establish a single source of truth for code ownership. This
prevents configuration drift and eliminates manual synchronization overhead
across distributed repositories.

### Summary

| Rule ID   | Principle / Constraint  | Priority | Primary Symptom / Trap     |
| :-------- | :---------------------- | :------- | :------------------------- |
| **T2-01** | Cross-Repository OWNERS | Medium   | Hardcoding a long list of  |
:           : File Deduplication via  :          : individual maintainer      :
:           : Include Directives      :          : emails in a shared or      :
:           :                         :          : cross-repo OWNERS file.    :
| **T2-02** | Cross-Repository OWNERS | Medium   | Manually copy-pasting the  |
:           : Centralization via      :          : email addresses of every   :
:           : Include Directives      :          : team member into multiple, :
:           :                         :          : independent repository     :
:           :                         :          : OWNERS files.              :

--------------------------------------------------------------------------------

### Rules

#### T2-01: Cross-Repository OWNERS File Deduplication via Include Directives

> **Rule:** Always use Gerrit's cross-repository `include` directive instead of
> hardcoding static maintainer lists for shared ownership groups.
>
> **What:** Ownership lists that apply across multiple repositories or broad
> directories must use Gerrit's cross-repository `include` directive rather than
> hardcoding static lists of individual maintainers.
>
> **Applies To:** BUILD_OWNERS or shared directory OWNERS files across
> distributed Gerrit repositories.
>
> **Why:** Maintaining duplicate static lists of owners across multiple
> repositories led to permission drift, stale owner lists, and manual
> synchronization overhead when team members joined or left the organization.
> Failing to adhere to this typically results in **Permission Drift / Stale
> ACLs**.

**Trap 1: Hardcoding a long list of individual maintainer emails in a shared or
cross-repo OWNERS file.**

**Don't:**

```text
# BAD: Hardcoding a list that will go stale
user1@google.com
user2@google.com
user3@chromium.org
```

**Do:**

```text
# GOOD: Using Gerrit's include syntax for a single source of truth
include build:/BUILD_OWNERS
```

--------------------------------------------------------------------------------

#### T2-02: Cross-Repository OWNERS Centralization via Include Directives

> **Rule:** Must centralize team member emails using Gerrit `include` directives
> to eliminate manual synchronization across repos.
>
> **What:** When managing ownership groups across multiple repositories,
> individual team member emails must be deduplicated by leveraging the Gerrit
> 'include' directive to point to a centralized source of truth.
>
> **Applies To:** OWNERS file configuration across distributed repositories
> managed by Gerrit's code-owners plugin.
>
> **Why:** Historically, maintaining manual lists of team members across
> multiple repository OWNERS files led to configuration drift, where departing
> or transferring team members retained access in some repos while losing it in
> others. Failing to adhere to this typically results in **Configuration
> Drift**.

**Trap 1: Manually copy-pasting the email addresses of every team member into
multiple, independent repository OWNERS files.**

**Don't:**

```text
# BAD: Hardcoded list of users
user1@chromium.org
user2@chromium.org
user3@chromium.org
```

**Do:**

```text
# GOOD: Include directive referencing a central file
include infra/owners/my_team_owners.txt
```

**Exceptions:** Single-repository, highly specialized subdirectories that
require unique, non-team-based granular ownership.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T1 | OWNERS File Configuration & Validation - *Provides the
    foundational syntax, validation, and domain rules required by the Gerrit
    code-owners plugin before cross-repo includes can be successfully resolved.*

## Chapter: Administrative Overrides & Emergency Bypasses

**Context:** This chapter governs the mechanisms and justifications for
bypassing standard OWNERS review gates, such as `owners-override` and `OO+1`.
These overrides must be strictly reserved for resolving critical infrastructure
failures, unblocking builds, or restoring review states when primary owners are
offline or tooling fails.

### Summary

| Rule ID   | Principle / Constraint   | Priority | Primary Symptom / Trap     |
| :-------- | :----------------------- | :------- | :------------------------- |
| **T3-01** | Administrative Overrides | Medium   | Waiting indefinitely for   |
:           : for OWNERS File Updates  :          : an administrative OWNERS   :
:           :                          :          : update to be reviewed      :
:           :                          :          : before landing a critical, :
:           :                          :          : unblocking code change.    :
| **T3-02** | Restoring Dropped Votes  | Medium   | Pinging a reviewer to      |
:           : via Owner Override       :          : re-apply a +1 after a      :
:           :                          :          : trivial rebase or          :
:           :                          :          : formatting fix clears      :
:           :                          :          : their vote.                :
| **T3-03** | Emergency OWNERS Bypass  | Critical | Blocking a critical,       |
:           : for Broken Pipelines     :          : verified build fix on a    :
:           :                          :          : standard geographic review :
:           :                          :          : cycle.                     :

--------------------------------------------------------------------------------

### Rules

#### T3-01: Administrative Overrides for OWNERS File Updates

> **Rule:** Must utilize administrative overrides to unblock urgent changelists
> only if simultaneously preparing the formal OWNERS file update in a parallel
> CL.
>
> **What:** Administrative overrides can be utilized to unblock urgent
> changelists that lack proper ownership approval, provided that the formal
> OWNERS file update is prepared simultaneously.
>
> **Applies To:** Gerrit code review gates, OWNERS enforcement plugins, and
> immediate pipeline unblocking workflows.
>
> **Why:** A developer needed to merge a change immediately to prevent project
> delays but lacked the required OWNERS entry. To avoid blocking the pipeline,
> they bypassed the gate while concurrently authoring an OWNERS update. Failing
> to adhere to this typically results in **Pipeline Blockage**.

**Trap 1: Waiting indefinitely for an administrative OWNERS update to be
reviewed before landing a critical, unblocking code change.**

**Don't:**

*   Allowing the CI pipeline or project to remain blocked for days while waiting
    for an independent OWNERS file modification to be reviewed and merged.

**Do:**

*   Utilize an administrative override to merge the critical code change
    immediately, while explicitly committing to and drafting the necessary
    OWNERS file update in a separate tracking CL.

**Exceptions:** Only acceptable if a follow-up OWNERS update is guaranteed or
during critical unblocking events.

--------------------------------------------------------------------------------

#### T3-02: Restoring Dropped Votes via Owner Override

> **Rule:** Always use the Owner Override (OO+1) feature to restore approval
> states dropped by trivial, non-logical patchset uploads.
>
> **What:** The Owner Override (OO+1) mechanism may be utilized by authorized
> personnel to restore an approval state that was mechanically dropped by a
> trivial patchset upload.
>
> **Applies To:** Gerrit review flows where non-logical updates (e.g., trivial
> rebases, typo fixes) reset reviewer +1 votes.
>
> **Why:** Trivial patchset uploads were mechanically resetting approval votes,
> forcing authors to wait for reviewers to return and re-acknowledge unchanged
> logic, unnecessarily stalling the pipeline. Failing to adhere to this
> typically results in **Stalled Review**.

**Trap 1: Pinging a reviewer to re-apply a +1 after a trivial rebase or
formatting fix clears their vote.**

**Don't:**

*   Waiting hours or days for a reviewer to click +1 again on a CL that has not
    logically changed.

**Do:**

*   Use the OO+1 (Owner Override) feature to manually carry forward the prior
    approval if authorized and confident the logic is identical.

**Exceptions:** Cannot be used if the new patchset contains substantive logical
changes or functional alterations.

--------------------------------------------------------------------------------

#### T3-03: Emergency OWNERS Bypass for Broken Pipelines

> **Rule:** Must invoke the `owners-override` mechanism to bypass standard
> review requirements when deploying urgent fixes for broken CI pipelines if
> primary owners are offline.
>
> **What:** The `owners-override` mechanism must be utilized to bypass standard
> OWNERS review requirements when deploying urgent fixes for broken CI/build
> pipelines if primary owners are offline.
>
> **Applies To:** Critical infrastructure paths, main branch CI pipelines (e.g.,
> build breakages), and operational configuration files.
>
> **Why:** Critical builds would remain broken for extended periods while
> waiting for specific, geographically distant owners to come online,
> effectively blocking all downstream development velocity for the entire team.
> Failing to adhere to this typically results in **Prolonged CI Pipeline
> Breakage**.

**Trap 1: Blocking a critical, verified build fix on a standard geographic
review cycle.**

**Don't:**

*   Waiting hours for primary owners to log in while the CI build pipeline
    remains completely broken.

**Do:**

*   Applying the 'owners-override' mechanism to the CL with explicit comments
    documenting that the build is broken and primary owners are unavailable.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T1 | OWNERS File Configuration & Validation - *Overrides are
    frequently used as an immediate fallback when missing or misconfigured
    OWNERS files prevent legitimate code merges.*
*   **Downstream:** T7 | Review Tooling Automation Quirks - *Gerrit
    auto-dropping votes on trivial patchsets necessitates the reactive use of
    the OO+1 override.*

## Chapter: Identity Verification & Access Control Enforcement

**Context:** Enforce strict reviewer identity verification and role-based
committer access within Gerrit workflows to prevent blocked continuous
integration pipelines. Unlinked corporate accounts and legacy group aliases must
be explicitly mapped to valid SSO identities to satisfy mandatory repository
submit rules.

### Summary

| Rule ID   | Principle / Constraint           | Priority | Primary Symptom /  |
:           :                                  :          : Trap               :
| :-------- | :------------------------------- | :------- | :----------------- |
| **T4-01** | Committer Access Verification    | High     | A non-committer    |
:           : for Final Submission Approval    :          : providing a final  :
:           :                                  :          : approval and       :
:           :                                  :          : assuming the       :
:           :                                  :          : change will        :
:           :                                  :          : auto-merge,        :
:           :                                  :          : leaving the author :
:           :                                  :          : stuck.             :
| **T4-02** | Strict Identity Verification for | High     | Providing a verbal |
:           : Review Stamps                    :          : LGTM but failing   :
:           :                                  :          : to recognize that  :
:           :                                  :          : the account lacks  :
:           :                                  :          : the necessary      :
:           :                                  :          : Gerrit permissions :
:           :                                  :          : to trigger a       :
:           :                                  :          : merge.             :
| **T4-03** | Corporate Account Linkage for    | High     | Attempting to use  |
:           : Code Approval                    :          : an unlinked        :
:           :                                  :          : open-source domain :
:           :                                  :          : account to provide :
:           :                                  :          : a mandatory        :
:           :                                  :          : employee/corporate :
:           :                                  :          : approval stamp.    :
| **T4-04** | Corporate Identity Linking for   | Critical | Attempting to      |
:           : Code-Review Privileges           :          : provide a formal   :
:           :                                  :          : +1 or LGTM on      :
:           :                                  :          : infrastructure     :
:           :                                  :          : repositories using :
:           :                                  :          : an unlinked        :
:           :                                  :          : community account. :
| **T4-05** | Verification of Committer Access | High     | Granting an LGTM   |
:           : via Linked Corporate Accounts    :          : from an account    :
:           :                                  :          : that lacks the     :
:           :                                  :          : necessary Gerrit   :
:           :                                  :          : permissions to     :
:           :                                  :          : merge code in that :
:           :                                  :          : specific           :
:           :                                  :          : repository.        :

--------------------------------------------------------------------------------

### Rules

#### T4-01: Committer Access Verification for Final Submission Approval

> **Rule:** Always explicitly tag an authorized committer for secondary approval
> if you lack the necessary repository permissions to trigger a merge.
>
> **What:** To submit changes in restricted repositories, approvals must come
> from individuals with explicit committer permissions. Secondary approvals must
> be explicitly sought if the primary reviewer lacks these rights.
>
> **Applies To:** Gerrit review process, specifically in repositories with
> strict committer access control (e.g., depot_tools).
>
> **Why:** Reviewers without explicit committer rights would approve a change,
> but the change remained blocked from landing, requiring a secondary review
> cycle from an authorized committer to satisfy enforcement checks. Failing to
> adhere to this typically results in **Blocked Submission / Policy Rejection**.

**Trap 1: A non-committer providing a final approval and assuming the change
will auto-merge, leaving the author stuck.**

**Don't:**

*   Reviewer grants LGTM and expects the change to merge automatically,
    completely ignoring their lack of committer status.

**Do:**

*   Reviewer grants LGTM and explicitly tags a known committer (e.g.,
    +committer@) to provide the secondary approval needed for submission.

--------------------------------------------------------------------------------

#### T4-02: Strict Identity Verification for Review Stamps

> **Rule:** Never attempt to stamp a change with an unlinked or unauthorized
> account; immediately redirect the review to an authorized approver.
>
> **What:** Reviewers who lack official project permissions or whose accounts
> are not linked correctly must explicitly redirect the review to an authorized
> approver rather than attempting to stamp the change.
>
> **Applies To:** Gerrit code review workflows requiring explicit +1/Code-Review
> and OWNERS approval stamps.
>
> **Why:** Reviewers transitioning out of project groups or utilizing unlinked
> personal accounts would attempt to approve changes, leaving the PR in a
> perpetually stuck state because the CI pipeline refused to recognize the
> stamp. Failing to adhere to this typically results in **Blocked CI Pipeline**.

**Trap 1: Providing a verbal LGTM but failing to recognize that the account
lacks the necessary Gerrit permissions to trigger a merge.**

**Don't:**

*   Providing a text-only "LGTM" in the comments without realizing the automated
    Gerrit score requires a verified committer stamp to unlock the pipeline.

**Do:**

*   Explicitly stating a lack of permissions and tagging an authorized committer
    to provide the formal stamp (e.g., "I lack voting permissions; +[committer]
    for the stamp.").

--------------------------------------------------------------------------------

#### T4-03: Corporate Account Linkage for Code Approval

> **Rule:** Must explicitly link open-source domain accounts to a corporate
> employee identity or route the CL to a verified employee to satisfy corporate
> approval requirements.
>
> **What:** External or unlinked open-source domain accounts are not recognized
> by internal tools (e.g., depot_tools) as having employee verification,
> requiring an explicitly linked corporate account or an alternative 'Googler
> stamp' to satisfy repository permissions.
>
> **Applies To:** depot_tools, Gerrit access controls, and strict
> ownership/committer boundaries in split internal/external repositories.
>
> **Why:** Contributors with @chromium.org accounts frequently encountered
> blocked submissions because internal tools did not recognize their accounts as
> corporate employees, leading to missing administrative approval requirements.
> Failing to adhere to this typically results in **Blocked Merge / Access
> Denied**.

**Trap 1: Attempting to use an unlinked open-source domain account to provide a
mandatory employee/corporate approval stamp.**

**Don't:**

*   Relying solely on an unlinked open-source account to approve a CL that
    strictly requires employee verification in the submit rules.

**Do:**

*   Ensure the open-source account is explicitly linked to a corporate account
    in the system configuration, or route the CL to a known verified employee to
    provide the secondary stamp.

--------------------------------------------------------------------------------

#### T4-04: Corporate Identity Linking for Code-Review Privileges

> **Rule:** Always cast approval votes using a Gerrit account tied directly to
> an internal corporate Single Sign-On (SSO) identity to bypass submit
> restrictions.
>
> **What:** Approvers must cast votes using a Gerrit account correctly linked to
> their internal corporate identity (SSO) to satisfy access control validation
> and infrastructure committer checks.
>
> **Applies To:** Gerrit account validation, OWNERS file configurations, and
> core infrastructure repositories (e.g., depot_tools) that mandate strict
> committer access.
>
> **Why:** System automation repeatedly rejected approvals from authorized
> reviewers because they voted from unlinked open-source accounts or through
> legacy group aliases without underlying committer rights, leaving CLs
> permanently blocked. Failing to adhere to this typically results in **Blocked
> Submission Pipeline**.

**Trap 1: Attempting to provide a formal +1 or LGTM on infrastructure
repositories using an unlinked community account.**

**Don't:**

*   Voting on restricted repositories via a standard open-source email account,
    causing the Gerrit Submit Rule to fail despite a valid +1.

**Do:**

*   Verify the account is linked to the corporate SSO identity and explicitly
    switch to the corporate email account to cast the formal approval vote.
    Infrastructure teams must provision correct committer rights to group-based
    aliases.

--------------------------------------------------------------------------------

#### T4-05: Verification of Committer Access via Linked Corporate Accounts

> **Rule:** Switch to your active corporate session with explicit committer
> access prior to issuing an LGTM to avoid creating procedural bottlenecks.
>
> **What:** Reviewers must ensure their active Gerrit session utilizes an
> identity (e.g., a specific @google.com vs @chromium.org account) that
> possesses explicit committer access for the target repository before issuing
> an LGTM.
>
> **Applies To:** Cross-repository code reviews involving split identities or
> external third-party repositories.
>
> **Why:** Approvals issued from secondary accounts lacking repository-specific
> committer rights create perceived procedural bottlenecks, leaving CLs
> unmergeable despite achieving technical consensus. Failing to adhere to this
> typically results in **Submission Blocked / Permission Denied**.

**Trap 1: Granting an LGTM from an account that lacks the necessary Gerrit
permissions to merge code in that specific repository.**

**Don't:**

*   Approving a CL with an unlinked or incorrect email identity, followed by a
    note stating 'lgtm, but I don't have committer access to this repo'.

**Do:**

*   Switching to the correct corporate account with committer privileges before
    stamping, confirming: 'Actually I guess my google account does, lgtm'.

**Exceptions:** When providing a purely technical review where another
authorized owner is explicitly expected to provide the final merge stamp.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T1 | OWNERS File Configuration & Validation - *Defines the
    underlying ownership and permission hierarchies that identity verification
    mechanisms enforce.*
*   **Downstream:** T5 | Multi-Tiered Approval Thresholds - *Requires valid,
    linked identity verification to satisfy multi-approval matrices and
    mandatory secondary +1s.*
*   **Downstream:** T7 | Review Tooling Automation Quirks - *Failures in
    identity linkage directly trigger blocked Gerrit Submit Rules and stalled
    auto-submit queues.*

## Chapter: Multi-Tiered Approval Thresholds

**Context:** This chapter dictates the enforcement of numerical and
domain-specific approval thresholds mandated by repository governance. It
defines the protocols for satisfying multi-tier sign-offs, such as acquiring
secondary committer votes, explicit domain team approvals, and verified employee
stamps, to successfully unblock CI pipelines.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap    |
| :-------- | :------------------------ | :------- | :------------------------ |
| **T5-01** | Strict Multi-Tiered       | High     | Assuming a single         |
:           : Approval Verification     :          : approval from a lead SME  :
:           :                           :          : is sufficient when        :
:           :                           :          : repository policy         :
:           :                           :          : mechanically enforces     :
:           :                           :          : multiple distinct +1s.    :
| **T5-02** | Mandatory Multi-Tiered &  | High     | Attempting to auto-submit |
:           : Domain-Specific Approvals :          : a CL after receiving a    :
:           :                           :          : single approval, ignoring :
:           :                           :          : the need for              :
:           :                           :          : multi-committer consensus :
:           :                           :          : or specialized team       :
:           :                           :          : reviews.                  :
| **T5-03** | Mandatory Multi-Tier      | High     | Attempting to merge a CL  |
:           : Approval Thresholds       :          : with only a single        :
:           :                           :          : technical approval when   :
:           :                           :          : the specific repository   :
:           :                           :          : governance requires two.  :

--------------------------------------------------------------------------------

### Rules

#### T5-01: Strict Multi-Tiered Approval Verification

> **Rule:** Always secure explicit numerical +1 votes in the review tooling;
> never rely on verbal deferrals to satisfy automated submit rules.
>
> **What:** When repository governance mandates a specific numerical threshold
> for approvals (e.g., two +1s), reviewers must provide explicit votes; implicit
> deferrals do not count toward the automated threshold.
>
> **Applies To:** Strict governance repositories (e.g., depot_tools) requiring
> multiple reviewer stamps for submission.
>
> **Why:** Authors were frequently left unable to land changes because secondary
> reviewers verbally deferred to a primary SME but failed to leave the required
> secondary +1 vote, stranding the CL in the validation phase. Failing to adhere
> to this typically results in **Blocked Submission**.

**Trap 1: Assuming a single approval from a lead SME is sufficient when
repository policy mechanically enforces multiple distinct +1s.**

**Don't:**

*   A secondary reviewer stating 'I defer to the other reviewer's LGTM' but
    failing to click the +1 button.

**Do:**

*   Explicitly coordinate to ensure the exact numerical threshold of +1 votes is
    met on the platform, proactively requesting the second stamp if a reviewer
    forgot to apply it.

--------------------------------------------------------------------------------

#### T5-02: Mandatory Multi-Tiered & Domain-Specific Approvals

> **Rule:** Must obtain elevated approval thresholds, including secondary
> committer votes and domain-specific sign-offs, before attempting to merge
> cross-cutting modifications.
>
> **What:** Certain changes dictate elevated approval thresholds, such as a
> secondary committer's +1 or a mandatory sign-off from specialized domain
> groups (e.g., Security, Source Team), which cannot be bypassed by a single
> general owner.
>
> **Applies To:** Core repository components, globally impacting infrastructure,
> or sensitive areas protected by custom submit rules.
>
> **Why:** Attempting to land cross-cutting or high-risk infrastructure changes
> with only a single general approval resulted in unauthorized structural
> modifications and regressions. Failing to adhere to this typically results in
> **Governance Violation**.

**Trap 1: Attempting to auto-submit a CL after receiving a single approval,
ignoring the need for multi-committer consensus or specialized team reviews.**

**Don't:**

*   Merging immediately upon receiving a single LGTM without verifying if a
    secondary committer or specific domain team +1 is required.

**Do:**

*   Holding the CL in review and explicitly requesting the required threshold
    (e.g., "Waiting for a second committer's +1" or "Needs approval from the
    Source team.").

--------------------------------------------------------------------------------

#### T5-03: Mandatory Multi-Tier Approval Thresholds

> **Rule:** Proactively track and satisfy explicit structural approval gates,
> such as a second approve or verified employee stamp, before attempting
> submission.
>
> **What:** Specific repositories enforce strict multi-tier approval counts
> (e.g., requiring two independent approvals, or a verified employee stamp) that
> must be structurally satisfied in the tooling before merge.
>
> **Applies To:** Codebase governance, Gerrit Submit Rules, and compliance
> pipelines.
>
> **Why:** Changes would sometimes linger indefinitely because authors assumed a
> single approval was sufficient, failing to realize the system architecture
> mandated a 'second approve' or a specific internal employee validation stamp
> to unlock the submit button. Failing to adhere to this typically results in
> **Blocked CI Submission**.

**Trap 1: Attempting to merge a CL with only a single technical approval when
the specific repository governance requires two.**

**Don't:**

*   Assuming a single technical LGTM from any contributor is sufficient to merge
    code, ignoring internal submit rules or employee verification constraints.

**Do:**

*   Proactively assign a secondary reviewer and explicitly track the 'second
    approve' or 'employee stamp' requirement in patchset comments to satisfy the
    tooling's threshold.

**Exceptions:** Repositories configured for single-approval (single +1)
workflows, or trivial documentation updates depending on team policy.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T4 | Identity Verification & Access Control Enforcement -
    *Dictates the validity of specific reviewer identities (e.g., Googler
    stamps) required to satisfy tooling thresholds.*
*   **Downstream:** T6 | Review Delegation & Domain Deferral - *Governs how
    reviewers delegate review tasks, which must still comply with explicit
    numerical vote requirements.*
*   **Downstream:** T7 | Review Tooling Automation Quirks - *Manages the
    side-effects when Gerrit submit rules stall or fail to recognize threshold
    approvals.*

## Chapter: Review Delegation & Domain Deferral

**Context:** This domain governs the explicit transition of review
responsibilities, mandating that reviewers lacking domain expertise or committer
rights formally defer to Subject Matter Experts (SMEs) or move to the CC list.
It enforces conditional approval mechanisms (LGTM %) and explicit non-blocking
declarations to maintain pipeline velocity without compromising technical rigor.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap    |
| :-------- | :------------------------ | :------- | :------------------------ |
| **T6-01** | Explicit Review Deferral  | Medium   | Leaving an informal LGTM  |
:           : and CC Transition for     :          : on a repository where the :
:           : Non-Committers            :          : reviewer lacks voting     :
:           :                           :          : rights, confusing the     :
:           :                           :          : author about the CL's     :
:           :                           :          : overall readiness.        :
| **T6-02** | Explicit Domain Deferral  | High     | Staying on an active      |
:           : by Non-SMEs               :          : review out of obligation  :
:           :                           :          : despite lacking the       :
:           :                           :          : technical context to      :
:           :                           :          : evaluate the underlying   :
:           :                           :          : code logic.               :
| **T6-03** | Explicit Delegation of    | Medium   | A generalist reviewer     |
:           : Technical Review to       :          : providing a blanket       :
:           : Domain Experts            :          : approval on highly        :
:           :                           :          : specialized subsystem     :
:           :                           :          : changes without           :
:           :                           :          : historical context.       :
| **T6-04** | Explicit Domain Deferral  | Medium   | Giving an unconditional   |
:           : for Partial Context       :          : +1 despite not            :
:           :                           :          : understanding the         :
:           :                           :          : underlying bug or the     :
:           :                           :          : system's operational      :
:           :                           :          : nuances.                  :
| **T6-05** | Conditional Review        | Medium   | Withholding an overall    |
:           : Delegation (LGTM %)       :          : approval flag entirely    :
:           :                           :          : due to minor nits or      :
:           :                           :          : waiting on another        :
:           :                           :          : reviewer's minor thread   :
:           :                           :          : to resolve.               :
| **T6-06** | Explicit Deferral to      | High     | Approving a change for    |
:           : Domain Experts            :          : syntax/style without      :
:           :                           :          : understanding the         :
:           :                           :          : underlying business logic :
:           :                           :          : or feature intent.        :
| **T6-07** | Explicit Review           | High     | Reviewing complex domain  |
:           : Delegation to Subject     :          : logic without sufficient  :
:           : Matter Experts            :          : expertise, providing a    :
:           :                           :          : surface-level approval.   :
| **T6-08** | Mandatory Domain Deferral | Medium   | Providing an LGTM on      |
:           : for Specialized           :          : specialized parameter     :
:           : Subsystems                :          : tuning without deep       :
:           :                           :          : domain knowledge.         :
| **T6-09** | Explicit Non-Blocking     | Medium   | Leaving extensive         |
:           : Declarations for          :          : comments without          :
:           : Unofficial Reviewers      :          : clarifying authorization  :
:           :                           :          : status, causing the       :
:           :                           :          : author to wait            :
:           :                           :          : indefinitely for an       :
:           :                           :          : approval stamp.           :
| **T6-10** | Conditional Approvals     | Medium   | Withholding an approval   |
:           : (LGTM %) for Unblocking   :          : entirely because of a     :
:           : Workflows                 :          : minor failing test or a   :
:           :                           :          : question best answered by :
:           :                           :          : another CC'd reviewer.    :

--------------------------------------------------------------------------------

### Rules

#### T6-01: Explicit Review Deferral and CC Transition for Non-Committers

> **Rule:** Always step down to the CC list when providing informal feedback on
> a repository where you lack formal committer rights. Never leave dangling
> LGTMs that create state ambiguity.
>
> **What:** Reviewers who provide informal feedback but lack formal committer
> rights in a given repository must explicitly step down from the 'Reviewer'
> role to the 'CC' list to prevent state ambiguity.
>
> **Applies To:** Cross-repository code reviews where peers lack explicit OWNERS
> or +1 permissions.
>
> **Why:** Informal LGTMs from non-committers left dangling in the Reviewer
> field created confusion over whether a CL had met its formal approval
> requirements, causing unnecessary merge delays. Failing to adhere to this
> typically results in **Approval Ambiguity**.

**Trap 1: Leaving an informal LGTM on a repository where the reviewer lacks
voting rights, confusing the author about the CL's overall readiness.**

**Don't:**

*   Reviewing a CL in a foreign repository and leaving a hanging 'LGTM' comment
    while remaining in the active Reviewers list.

**Do:**

*   Provide the review feedback and explicitly move from the 'Reviewer' list to
    the 'CC' list, stating the lack of permissions to finalize the approval.

--------------------------------------------------------------------------------

#### T6-02: Explicit Domain Deferral by Non-SMEs

> **Rule:** Automatically assigned reviewers must immediately remove themselves
> and defer to a Subject Matter Expert (SME) if they lack technical context.
> Avoid providing rubber-stamp approvals out of obligation.
>
> **What:** Individuals automatically assigned to a review (e.g., build
> gardeners) must explicitly remove themselves and defer to a Subject Matter
> Expert (SME) if they lack the technical context to evaluate the change.
>
> **Applies To:** Automated reviewer routing, gardener rotations, and incident
> response reviews.
>
> **Why:** Gardeners were automatically assigned to reviews during
> infrastructure breakages. Some approved complex fixes they did not fully
> understand, risking the introduction of flawed patches due to superficial
> validation. Failing to adhere to this typically results in **Superficial
> Approval**.

**Trap 1: Staying on an active review out of obligation despite lacking the
technical context to evaluate the underlying code logic.**

**Don't:**

*   A gardener or casually added reviewer providing a rubber-stamp LGTM without
    understanding the root cause of the fix.

**Do:**

*   Explicitly state the lack of domain knowledge, remove oneself from the
    reviewer list, and suggest or defer to a qualified Subject Matter Expert.

**Exceptions:** Emergency rollbacks where verifying the code diff is less
critical than restoring system stability.

--------------------------------------------------------------------------------

#### T6-03: Explicit Delegation of Technical Review to Domain Experts

> **Rule:** Must explicitly defer final technical evaluation to SMEs when
> reviewing complex or highly specialized components. Never provide blanket
> approvals without historical subsystem context.
>
> **What:** Reviewers assigned to complex or highly specialized components
> (e.g., core infrastructure, metrics, or filesystems) must explicitly defer
> technical evaluation to Subject Matter Experts (SMEs) if they lack deep
> historical context.
>
> **Applies To:** Code review assignment and approval phases, particularly for
> specialized subsystems.
>
> **Why:** Approving complex domain-specific code without specialized context
> risked regressions or architectural misalignment. Reviewers adopted a pattern
> of blocking their own final approval pending a stamp from a designated SME.
> Failing to adhere to this typically results in **Domain Regression /
> Architectural Misalignment**.

**Trap 1: A generalist reviewer providing a blanket approval on highly
specialized subsystem changes without historical context.**

**Don't:**

*   Providing a +1 on core filesystem or legacy metrics code without consulting
    the original authors or domain experts.

**Do:**

*   Explicitly tagging an SME (e.g., '+SME for btrfs review') and conditionally
    stating 'Will stamp if it looks good to them'.

**Exceptions:** Minor typographical, documentation, or formatting fixes that do
not affect subsystem logic.

--------------------------------------------------------------------------------

#### T6-04: Explicit Domain Deferral for Partial Context

> **Rule:** Always segment approval scopes by validating syntax while explicitly
> deferring the formal domain sign-off (CR+1) to an SME when lacking historical
> context.
>
> **What:** Reviewers must explicitly segment their approval, validating syntax
> and code health while actively deferring formal domain sign-off (CR+1) to a
> Subject Matter Expert (SME) if they lack full historical or systemic context.
>
> **Applies To:** Code reviews spanning multiple domains or touching complex
> legacy systems where an assigned reviewer lacks deep expertise.
>
> **Why:** Generalist reviewers were fully approving complex domain changes
> (like Browser CI logic) based purely on syntax, inadvertently letting subtle
> logic regressions slip into production because the SME was never invoked.
> Failing to adhere to this typically results in **Unvetted Domain
> Regressions**.

**Trap 1: Giving an unconditional +1 despite not understanding the underlying
bug or the system's operational nuances.**

**Don't:**

*   Applying a full CR+1/OWNERS stamp when the reviewer does not understand the
    historical context of the bug being fixed.

**Do:**

*   Issuing a conditional soft-approval (LGTM for syntax) but explicitly leaving
    the formal +1 to the SME (e.g., "LGTM, but leaving CR+1 to [SME] who knows
    [System] better").

--------------------------------------------------------------------------------

#### T6-05: Conditional Review Delegation (LGTM %)

> **Rule:** Use conditional 'LGTM %' syntax to unblock the state machine while
> explicitly deferring technical enforcement to other reviewers. Never stall a
> pipeline over minor localized nits.
>
> **What:** Reviewers use the conditional syntax to grant structural approval
> while explicitly deferring to another reviewer for specific technical
> enforcement or minor fixes, preventing pipeline stalls.
>
> **Applies To:** Multi-reviewer Gerrit workflows, consensus building, and
> cross-timezone review coordination.
>
> **Why:** Reviewers frequently held up final approvals for minor typos or
> out-of-domain threads, causing significant delays. The conditional LGTM syntax
> was adopted to unblock the state machine while retaining technical rigor.
> Failing to adhere to this typically results in **Stalled Code Review**.

**Trap 1: Withholding an overall approval flag entirely due to minor nits or
waiting on another reviewer's minor thread to resolve.**

**Don't:**

*   Refusing to grant approval because of a single typo, or letting the CL stall
    because a different reviewer has an open thread about a localized component.

**Do:**

*   Issue a conditional approval using the 'LGTM % [condition]' or 'LGTM %
    [reviewer]' syntax to structurally unblock the CL state while strictly
    enforcing that the author resolves the delegated condition before
    submission.

**Exceptions:** Critical security reviews or architectural shifts where all
stakeholders must definitively agree without conditions.

--------------------------------------------------------------------------------

#### T6-06: Explicit Deferral to Domain Experts

> **Rule:** Always enforce informative commit descriptions and require SME
> stamps before conducting syntax reviews on complex features. Never approve
> logic based purely on syntactic correctness.
>
> **What:** Reviewers lacking sufficient domain context for a feature must
> explicitly defer approval to a Subject Matter Expert (SME) rather than
> rubber-stamping the CL based on syntactic correctness alone.
>
> **Applies To:** Complex feature reviews (e.g., authentication, security keys,
> hardware integration) and bug fix validations.
>
> **Why:** Historically, reviewers would approve CLs because the code syntax
> looked clean, inadvertently authorizing logic flaws in critical systems
> because they did not understand the underlying business or feature intent.
> This rule forces reviewers to enforce informative commit descriptions and
> require SME stamps first. Failing to adhere to this typically results in
> **Rubber-Stamping / Unverified Logic**.

**Trap 1: Approving a change for syntax/style without understanding the
underlying business logic or feature intent.**

**Don't:**

*   Granting a +1 or +2 based solely on code formatting, Python syntax, or
    general best practices when the reviewer does not understand the actual
    feature being modified.

**Do:**

*   Explicitly request an SME stamp first, declare a lack of context, and
    enforce that the CL description is populated with deep technical intent
    before conducting a syntax review.

**Exceptions:** Trivial mechanical refactors (e.g., global renames or automated
lint fixes) that do not alter business logic.

--------------------------------------------------------------------------------

#### T6-07: Explicit Review Delegation to Subject Matter Experts

> **Rule:** Reviewers lacking sufficient domain context must formally step down
> to CC and tag an SME for the component. Avoid conducting surface-level reviews
> on complex domain logic.
>
> **What:** Reviewers assigned to a changelist without sufficient domain context
> must formally step down to CC or explicitly defer technical validation to a
> designated SME to prevent shallow approvals.
>
> **Applies To:** Gerrit review workflows, specifically when evaluating complex
> components, build infrastructure, or developer tooling UX.
>
> **Why:** Historically, assigning reviewers outside their primary expertise led
> to unverified logic changes being merged. Teams adopted a strict policy of
> acknowledging context gaps and tagging domain experts rather than blocking the
> review or falsely approving it. Failing to adhere to this typically results in
> **Shallow Technical Audit**.

**Trap 1: Reviewing complex domain logic without sufficient expertise, providing
a surface-level approval.**

**Don't:**

*   Proceeding with a standard LGTM vote despite lacking deep context on the
    system's architecture or expected behavior.

**Do:**

*   Explicitly state a lack of domain context, optionally move oneself to CC,
    and tag the appropriate Subject Matter Expert for the component to conduct
    the formal review.

**Exceptions:** When verifying purely formatting changes, trivial syntactic
updates, or if the reviewer is granting an informal '+1 of non-ownership'
strictly for moral support.

--------------------------------------------------------------------------------

#### T6-08: Mandatory Domain Deferral for Specialized Subsystems

> **Rule:** Must defer parameter tuning and configuration changes to designated
> SMEs. Never blindly approve cross-cutting changes in specialized operational
> subsystems.
>
> **What:** Assigned reviewers lacking specific operational context (e.g.,
> parameter tuning, recipe execution) must explicitly defer to designated
> Subject Matter Experts (SMEs) rather than blindly approving.
>
> **Applies To:** Cross-cutting CLs affecting specialized configurations (e.g.,
> Rclient tuning, build recipes).
>
> **Why:** Generalist reviewers were approving configuration changes in complex
> subsystems, leading to unvetted parameter tuning that caused performance
> regressions. Failing to adhere to this typically results in **Unvetted
> Configuration Regressions**.

**Trap 1: Providing an LGTM on specialized parameter tuning without deep domain
knowledge.**

**Don't:**

*   A general maintainer reviewing and immediately approving a complex Rclient
    parameter adjustment without SME input.

**Do:**

*   The general maintainer explicitly comments 'Let me defer to the [Component]
    owner' and adds the SME to the review.

--------------------------------------------------------------------------------

#### T6-09: Explicit Non-Blocking Declarations for Unofficial Reviewers

> **Rule:** Unofficial contributors providing feedback on a thread must
> explicitly declare that their input does not block the submission.
>
> **What:** Contributors who participate in a review thread but lack formal
> blocking reviewer privileges must explicitly state that their feedback does
> not gate the submission.
>
> **Applies To:** Gerrit discussion threads where drive-by contributors, subject
> matter experts, or CC'd engineers provide feedback.
>
> **Why:** Authors frequently experience idle time waiting for an 'LGTM' from
> every participant in a thread, stalling the CI pipeline because they
> misunderstand who holds official blocking reviewer status. Failing to adhere
> to this typically results in **Pipeline Stagnation / Idle Time**.

**Trap 1: Leaving extensive comments without clarifying authorization status,
causing the author to wait indefinitely for an approval stamp.**

**Don't:**

*   Providing detailed feedback and silently stepping away, leaving the author
    assuming they must wait for your LGTM to proceed.

**Do:**

*   Explicitly stating: 'I am not a reviewer on this change, do not wait for an
    LGTM from me to land.'

--------------------------------------------------------------------------------

#### T6-10: Conditional Approvals (LGTM %) for Unblocking Workflows

> **Rule:** Utilize conditional approvals ('LGTM %') to delegate final
> verification of localized fixes or tests. Avoid withholding a CR+1 for
> isolated domain questions.
>
> **What:** Reviewers should utilize conditional approvals ('LGTM %') to unblock
> changes pending minor localized fixes or domain-specific sign-offs from other
> reviewers, rather than stalling the entire CL.
>
> **Applies To:** Gerrit reviews where primary logic is sound but minor issues
> (tests, formatting) or localized domain concerns remain.
>
> **Why:** Requiring a full synchronous re-review for trivial test fixes or
> isolated domain questions significantly slows down development velocity.
> Failing to adhere to this typically results in **Pipeline Stagnation**.

**Trap 1: Withholding an approval entirely because of a minor failing test or a
question best answered by another CC'd reviewer.**

**Don't:**

*   Holding the CR+1 hostage until a single minor test is fixed or another
    reviewer comments, forcing a complete asynchronous review cycle later.

**Do:**

*   Granting a conditional approval delegating the final checks: 'lgtm % fixing
    tests' or 'lgtm % Robbie's comments'.

**Exceptions:** Core architectural flaws or security issues cannot be
conditionally approved.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T4 | Identity Verification & Access Control Enforcement -
    *Dictates the explicit committer privileges and roles that necessitate
    formal review delegation.*
*   **Upstream:** T1 | OWNERS File Configuration & Validation - *Defines the
    baseline repository permissions that dictate when a reviewer must defer to
    CC due to lack of voting rights.*
*   **Downstream:** T8 | Review Pipeline State & Meta-Coordination - *Relies on
    conditional LGTMs and explicit deferrals to prevent pipeline stagnation and
    efficiently manage changelist lifecycles.*
*   **Downstream:** T5 | Multi-Tiered Approval Thresholds - *Depends on accurate
    SME delegations to fulfill specialized team sign-offs required by repository
    governance.*

## Chapter: Review Tooling Automation Quirks

**Context:** This chapter governs the mitigation of platform side-effects and
automation bugs within the Gerrit code review ecosystem. It dictates how to
navigate UI limitations, stuck submit rules, and interrupted auto-submit queues
to maintain CI pipeline velocity.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap    |
| :-------- | :------------------------ | :------- | :------------------------ |
| **T7-01** | Escalation of Stuck       | Medium   | Staring at an unsatisfied |
:           : Submit Rules              :          : Submit Rule without a     :
:           :                           :          : clear reason and waiting  :
:           :                           :          : for it to eventually      :
:           :                           :          : resolve itself.           :
| **T7-02** | External Contributor      | Medium   | Assuming an external      |
:           : OWNERS Verification       :          : owner can immediately     :
:           : Limitations               :          : approve changes without   :
:           :                           :          : internal corporate        :
:           :                           :          : accounts.                 :
| **T7-03** | Gerrit Auto-Submit        | Medium   | Assuming the CL will      |
:           : Interruption via Resolved :          : automatically merge after :
:           : Comments                  :          : resolving all comments    :
:           :                           :          : while the auto-submit     :
:           :                           :          : flag was previously       :
:           :                           :          : enabled.                  :

--------------------------------------------------------------------------------

### Rules

#### T7-01: Escalation of Stuck Submit Rules

> **Rule:** Always escalate unexplained or bugged Submit Rule evaluations via
> formal bug reports rather than silently waiting for spontaneous resolution.
>
> **What:** Unexplained or bugged Submit Rule evaluations that block a CL from
> merging must be escalated via a formal bug report rather than silently waiting
> or repeatedly retrying.
>
> **Applies To:** Gerrit Submit Rule (SR) engine, Review-Enforcement plugins,
> and CI gating infrastructure.
>
> **Why:** The Review-Enforcement Submit Rule remained stuck in an unsatisfied
> state despite all conditions being met, silently preventing submission until
> an engineer filed a bug to track the infrastructure failure. Failing to adhere
> to this typically results in **CI Pipeline Lockup**.

**Trap 1: Staring at an unsatisfied Submit Rule without a clear reason and
waiting for it to eventually resolve itself.**

**Don't:**

*   Repeatedly rebasing or adding trivial comments to trigger a re-evaluation of
    a stuck Gerrit Submit Rule.

**Do:**

*   File a bug against the Gerrit or infrastructure team immediately, linking
    the issue tracker in the CL comments for transparency.

--------------------------------------------------------------------------------

#### T7-02: External Contributor OWNERS Verification Limitations

> **Rule:** Never assume external contributors added to OWNERS files possess
> standard Gerrit UI affordances (like the +1 button) without verifying
> corporate account permissions.
>
> **What:** External contributors added to OWNERS files may lack standard Gerrit
> UI affordances (like the Code-Review +1 button) due to missing internal group
> permissions, necessitating administrative awareness and tooling fixes.
>
> **Applies To:** Gerrit UI and repository tooling (e.g., depot_tools) when
> managing external/non-corporate code owners.
>
> **Why:** External maintainers were added to OWNERS files but could not
> exercise their approval rights because the code review UI did not expose the
> +1 button to accounts lacking internal group memberships. Failing to adhere to
> this typically results in **UI Limitation / Blocked Approvals**.

**Trap 1: Assuming an external owner can immediately approve changes without
internal corporate accounts.**

**Don't:**

*   Adding an external user to OWNERS and expecting them to hit the +1 button
    unassisted in standard Gerrit UI.

**Do:**

*   Acknowledging the UI limitation for external contributors and actively
    tracking a fix or providing a proxy approval workflow.

--------------------------------------------------------------------------------

#### T7-03: Gerrit Auto-Submit Interruption via Resolved Comments

> **Rule:** Always manually verify and re-enable the Auto-Submit toggle when
> simultaneously replying to and resolving a comment thread in Gerrit.
>
> **What:** A known quirk in the Gerrit code review tooling where submitting a
> reply and resolving a comment thread simultaneously disables the 'Auto-Submit'
> flag, halting the CI pipeline.
>
> **Applies To:** Gerrit Code Review Platform; CI/CD auto-submit queues.
>
> **Why:** Reviewers and authors noticed that successfully approved changelists
> were stalling in the merge queue. This was traced back to authors replying to
> feedback and clicking 'Resolve' in the same UI action, which silently toggles
> off the Gerrit auto-submit functionality. Failing to adhere to this typically
> results in **Stalled CI Pipeline**.

**Trap 1: Assuming the CL will automatically merge after resolving all comments
while the auto-submit flag was previously enabled.**

**Don't:**

*   Replying to a code review comment and checking the 'Resolve' button
    simultaneously on a Gerrit thread when Auto-Submit is enabled, without
    verifying the pipeline state afterward.

**Do:**

*   Separate the reply and resolution actions into distinct steps, or manually
    verify and re-enable the Auto-Submit toggle after resolving threads to
    ensure the merge queue isn't blocked.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T4 | Identity Verification & Access Control Enforcement -
    *Underlying internal group permission models dictate whether standard UI
    features like the +1 button are rendered for external accounts.*
*   **Upstream:** T9 | External Contributor Facilitation - *External
    contributors requiring administrative intervention due to UI limitations
    must be facilitated by maintainers.*
*   **Downstream:** T8 | Review Pipeline State & Meta-Coordination - *Managing
    stalled queues and meta-coordinating bug tracking for broken platform
    integrations.*

## Chapter: Review Pipeline State & Meta-Coordination

**Context:** This chapter governs the lifecycle management of changelists (CLs),
dictating strict protocols for patchset history demarcation, retrospective
reviews, and auto-submit state management. Adherence ensures CI pipeline
velocity without compromising auditability or reviewer context.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap    |
| :-------- | :------------------------ | :------- | :------------------------ |
| **T8-01** | Suspension of Auto-Submit | High     | Approving and submitting  |
:           : for Unavailable Authors   :          : a CL simply because the   :
:           :                           :          : code is correct, ignoring :
:           :                           :          : the author's current      :
:           :                           :          : availability status.      :
| **T8-02** | Patchset Lifecycle and    | High     | Silently pushing a new    |
:           : Rebase State Declaration  :          : patchset after a broken   :
:           :                           :          : rebase without notifying  :
:           :                           :          : the reviewer that tests   :
:           :                           :          : were dropped or changes   :
:           :                           :          : were overwritten.         :
| **T8-03** | Post-Merge Retrospective  | Medium   | Merging a fast-tracked    |
:           : Reviews                   :          : change and abandoning the :
:           :                           :          : code review thread        :
:           :                           :          : without formalizing the   :
:           :                           :          : oversight.                :
| **T8-04** | Explicit Demarcation of   | Medium   | Uploading a mix of        |
:           : Patchset History          :          : formatting and logic      :
:           :                           :          : changes across multiple   :
:           :                           :          : patchsets without         :
:           :                           :          : providing a guide for the :
:           :                           :          : reviewer.                 :
| **T8-05** | Mandatory Reland Tagging  | High     | Submitting a reland CL    |
:           : for Original Reverters    :          : with only new reviewers.  :
| **T8-06** | Mandatory Re-Review After | High     | Landing a heavily         |
:           : Significant Post-Approval :          : modified patchset based   :
:           : Code Churn                :          : on a stale CR+1 from an   :
:           :                           :          : earlier patchset.         :

--------------------------------------------------------------------------------

### Rules

#### T8-01: Suspension of Auto-Submit for Unavailable Authors

> **Rule:** Always withhold the auto-submit trigger if the changelist author is
> marked Out Of Office (OOO) to prevent unmonitored build breakages.
>
> **What:** Reviewers must manually withhold the auto-submit trigger if the CL
> author is Out Of Office (OOO) to ensure someone is available to monitor the
> rollout and respond to breakages.
>
> **Applies To:** Commit Queue (CQ), auto-submit workflows, and final CL
> approval states.
>
> **Why:** Automatically submitting code while the original author was
> unavailable resulted in untriaged build breakages, as no one had the
> historical context to quickly revert or fix the regression. Failing to adhere
> to this typically results in **Unmonitored Build Breakage**.

**Trap 1: Approving and submitting a CL simply because the code is correct,
ignoring the author's current availability status.**

**Don't:**

*   Setting Auto-Submit+1 or CQ+2 on a CL when the author's Gerrit status is
    clearly marked as OOO.

**Do:**

*   Provide the +1/LGTM approval to signal code readiness, but explicitly
    withhold the auto-submit trigger, leaving a comment noting the author's OOO
    status.

**Exceptions:** If the reviewer assumes full responsibility for monitoring the
rollout and fixing any resulting breakages.

--------------------------------------------------------------------------------

#### T8-02: Patchset Lifecycle and Rebase State Declaration

> **Rule:** Must explicitly document the patchset delta when pushing significant
> structural changes or manual rebases to prevent silent loss of test coverage.
>
> **What:** Authors must explicitly document and communicate the state of
> patchsets, particularly when rebases drop tests or when a change-ID is
> accidentally hijacked/overridden by new content.
>
> **Applies To:** Gerrit patchset updates, resolving rebase conflicts, and
> handling unintended overwrites of Change-IDs.
>
> **Why:** Authors were silently force-pushing updates after botching a rebase,
> resulting in dropped tests or completely overwritten logic. Reviewers would
> mistakenly approve the delta without realizing structural components were
> lost. Failing to adhere to this typically results in **Incomplete Test
> Coverage**.

**Trap 1: Silently pushing a new patchset after a broken rebase without
notifying the reviewer that tests were dropped or changes were overwritten.**

**Don't:**

*   Running `git commit --amend` and pushing a heavily altered rebase without
    leaving a comment explaining the patchset delta.

**Do:**

*   Explicitly commenting on the new patchset to notify reviewers of the error
    and correction (e.g., "Missed a test during rebase, PTAL at PS6").

**Exceptions:** Trivial, non-conflicting auto-rebases performed by the Gerrit
UI.

--------------------------------------------------------------------------------

#### T8-03: Post-Merge Retrospective Reviews

> **Rule:** Never bypass the standard review pipeline without formally logging a
> retrospective review and a tracking ticket on the associated patchset.
>
> **What:** Changes landed outside the standard review pipeline (e.g.,
> emergencies or procedural bypasses) must have a formal retrospective review
> logged on the patchset with an associated tracking ticket to maintain
> structural auditability.
>
> **Applies To:** CI pipeline bypasses, emergency infrastructure fixes, and
> delayed review governance workflows.
>
> **Why:** To preserve audit compliance without sacrificing deployment velocity
> during incidents, teams developed a post-mortem workflow where the code is
> reviewed post-merge and bound to an explicit tracking ticket. Failing to
> adhere to this typically results in **Audit Compliance Failure**.

**Trap 1: Merging a fast-tracked change and abandoning the code review thread
without formalizing the oversight.**

**Don't:**

*   Submitting a CL in an emergency or via a bypass without a documented
    retrospective review stamp or an attached tracking ticket.

**Do:**

*   Add a patchset-level comment explicitly stating 'retro review' along with
    the tracking bug ID (e.g., b/[Issue-ID]) after the merge, ensuring reviewers
    officially acknowledge the state.

**Exceptions:** Standard CLs that pass through the normal pre-commit review
gates.

--------------------------------------------------------------------------------

#### T8-04: Explicit Demarcation of Patchset History

> **Rule:** Always isolate formatting changes from logic changes across separate
> patchsets and provide a clear meta-diff guide for reviewers.
>
> **What:** Changelist authors must document the specific purpose of distinct
> patchsets using meta-comments to isolate noisy refactoring or reverts from
> functional logic changes.
>
> **Applies To:** Iterative code reviews in Gerrit involving multiple complex
> patchsets, formatting updates, or revert/re-apply cycles.
>
> **Why:** Reviewers frequently wasted time attempting to audit raw diffs that
> included mass formatting changes or intermediate revert states. Authors
> adopted a convention of providing a 'meta-diff guide' to narrow the review
> scope. Failing to adhere to this typically results in **Reviewer Fatigue**.

**Trap 1: Uploading a mix of formatting and logic changes across multiple
patchsets without providing a guide for the reviewer.**

**Don't:**

*   Silently uploading a patchset containing widespread formatting alongside
    subtle functional changes, forcing the reviewer to parse the entire diff
    indiscriminately.

**Do:**

*   Separate formatting concerns chronologically into a standalone patchset and
    add a thread comment explicitly stating which diff range to evaluate (e.g.,
    'PS1 - original, PS2 pure formatting, actual change PS2..PS4.').

**Exceptions:** Trivial, single-file CLs where the entirety of the diff is
self-evident.

--------------------------------------------------------------------------------

#### T8-05: Mandatory Reland Tagging for Original Reverters

> **Rule:** Must explicitly tag the engineer who executed the original revert
> when submitting a reland patch.
>
> **What:** When relanding a previously reverted changelist, the engineer who
> executed the original revert must be explicitly tagged in the new review
> thread.
>
> **Applies To:** Any changelist that resurrects, reverts, or re-implements
> logic that was previously rolled back.
>
> **Why:** Authors would silently reland previously problematic code without
> consulting the engineer who discovered the original breakage, often
> re-introducing the exact same regressions. Failing to adhere to this typically
> results in **Repeated Production Regressions**.

**Trap 1: Submitting a reland CL with only new reviewers.**

**Don't:**

*   Sending a fix for a reverted CL to a random team member for quick approval
    without notifying past stakeholders.

**Do:**

*   Adding the engineer who reverted the original patchset to the CC list with a
    comment explaining how the previous issue was mitigated.

--------------------------------------------------------------------------------

#### T8-06: Mandatory Re-Review After Significant Post-Approval Code Churn

> **Rule:** Always request a fresh review if significant modifications are made
> to a changelist after receiving an initial Code-Review+1 approval.
>
> **What:** Previous Code-Review+1 (CR+1) approvals are implicitly invalidated
> if substantial code modifications occur before the changelist lands. A
> re-review must be explicitly requested before merging.
>
> **Applies To:** All Gerrit code reviews prior to the final submit phase,
> particularly when patchsets are uploaded after an initial approval.
>
> **Why:** Historically, large refactors or logic additions pushed after an
> initial CR+1 could introduce unreviewed bugs or unintended side-effects into
> the main branch if landed relying on the outdated stamp. Failing to adhere to
> this typically results in **Unreviewed Code Merged**.

**Trap 1: Landing a heavily modified patchset based on a stale CR+1 from an
earlier patchset.**

**Don't:**

*   Submitting patchset 24 using a CR+1 granted on patchset 10 without notifying
    the original reviewer of the intermediate changes.

**Do:**

*   Explicitly ping the original reviewer requesting a re-review of the delta:
    'You gave CR+1 earlier, but a lot of code has changed. Requesting a quick
    re-review before landing.'

**Exceptions:** Minor structural changes, typo fixes, or trivial rebases that do
not alter core logic.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T3 | Administrative Overrides & Emergency Bypasses -
    *Determines when standard pipeline workflows and formal retrospective review
    requirements are triggered due to emergency bypasses.*
*   **Downstream:** T7 | Review Tooling Automation Quirks - *Managing Gerrit
    auto-submit logic heavily relies on the correct manual overrides of pipeline
    states.*

## Chapter: External Contributor Facilitation

**Context:** Core maintainers must actively proxy Continuous Integration (CI)
execution, validate Contributor License Agreements (CLAs), and manually merge
changelists on behalf of external or unpermissioned authors. This administrative
facilitation prevents approved open-source contributions from stalling
indefinitely due to internal access restrictions.

### Summary

| Rule ID   | Principle /           | Priority | Primary Symptom / Trap        |
:           : Constraint            :          :                               :
| :-------- | :-------------------- | :------- | :---------------------------- |
| **T9-01** | Proxying CQ+2         | Medium   | Telling a permissionless      |
:           : Submissions for       :          : author to 'click submit' once :
:           : Permission-Restricted :          : approved, leaving them unable :
:           : Authors               :          : to proceed.                   :
| **T9-02** | External Contributor  | Critical | Approving an external CL but  |
:           : Process Facilitation  :          : failing to merge it, leaving  :
:           :                       :          : the external contributor      :
:           :                       :          : stranded without repository   :
:           :                       :          : access.                       :
| **T9-03** | Proxy CI Execution    | Medium   | Leaving an approved external  |
:           : and Merging for       :          : contribution idle, expecting  :
:           : External Contributors :          : the author to finalize the CI :
:           :                       :          : and merge process.            :
| **T9-04** | Proxy Submission for  | Medium   | Expecting an external         |
:           : External Contributors :          : changelist to auto-submit     :
:           :                       :          : without internal              :
:           :                       :          : administrative intervention.  :

--------------------------------------------------------------------------------

### Rules

#### T9-01: Proxying CQ+2 Submissions for Permission-Restricted Authors

> **Rule:** Always apply the 'CQ+2' vote on behalf of external authors who lack
> commit queue privileges to prevent their patches from stalling.
>
> **What:** Reviewers with commit queue (CQ) privileges must manually trigger
> the 'CQ+2' vote on behalf of external or unpermissioned authors to initiate
> the merge pipeline.
>
> **Applies To:** Commit Queue (CQ) submission phase in Gerrit for external or
> first-time contributors.
>
> **Why:** Authors who lacked direct submit permissions would get stuck after
> receiving approvals, requiring maintainers to intervene and proxy the commit
> queue vote to unblock the build. Failing to adhere to this typically results
> in **Stalled Pipeline / Unsubmitted CL**.

**Trap 1: Telling a permissionless author to 'click submit' once approved,
leaving them unable to proceed.**

**Don't:**

*   Instructing the author to use the Gerrit 'submit' button when they do not
    belong to the required committer groups.

**Do:**

*   Applying the 'CQ+2' vote directly on behalf of the author to initiate the
    automated merge pipeline.

**Exceptions:** Authors who have been granted direct committer privileges.

--------------------------------------------------------------------------------

#### T9-02: External Contributor Process Facilitation

> **Rule:** Must validate CLA signatures, enforce project commit standards, and
> execute the final merge for external contributors who lack repository access.
>
> **What:** Core maintainers must actively facilitate the landing of external
> contributions by validating CLA signatures, enforcing standard commit
> messages, and executing the merge on behalf of the contributor who lacks
> permissions.
>
> **Applies To:** Open-source pull requests or Gerrit changes originating from
> external authors without committer/landing access.
>
> **Why:** External contributors were blocked from contributing because they did
> not have access to internal repository landing commands, or their CLs violated
> legal bounds due to missing CLA checks. Failing to adhere to this typically
> results in **Compliance Violation**.

**Trap 1: Approving an external CL but failing to merge it, leaving the external
contributor stranded without repository access.**

**Don't:**

*   Adding a +1 to an external contributor's CL and expecting them to invoke the
    commit queue themselves.

**Do:**

*   Verifying the CLA, ensuring the commit message matches project standards,
    and invoking the merge (landing) on the external contributor's behalf.

--------------------------------------------------------------------------------

#### T9-03: Proxy CI Execution and Merging for External Contributors

> **Rule:** Manually trigger required automated tests, perform necessary
> rebases, and land approved changes for first-time contributors who lack
> internal CI permissions.
>
> **What:** Repository maintainers and reviewers must act as operational proxies
> for first-time or external contributors, manually executing rebases,
> triggering automated tests, and landing approved changes.
>
> **Applies To:** Open-source pull requests and external Gerrit changelists from
> first-time contributors.
>
> **Why:** External contributors natively lack the internal CI permissions
> required to trigger pre-submit bots or execute merge operations, leading to
> approved CLs stalling indefinitely without manual intervention. Failing to
> adhere to this typically results in **Stalled Integration**.

**Trap 1: Leaving an approved external contribution idle, expecting the author
to finalize the CI and merge process.**

**Don't:**

*   Approving a CL from a first-time contributor and leaving the thread without
    triggering the commit queue or merging the patch.

**Do:**

*   The designated reviewer must manually trigger relevant automated tests,
    perform necessary patch rebases, and execute the final merge/land operation
    on behalf of the author.

**Exceptions:** Contributors who have formally signed the CLA and have
subsequently been granted committer or tryjob trigger access.

--------------------------------------------------------------------------------

#### T9-04: Proxy Submission for External Contributors

> **Rule:** Explicitly merge approved changelists for external contributors
> using internal permissions to finalize integration.
>
> **What:** External contributors lacking repository committer permissions
> require an internal reviewer to explicitly merge approved changelists on their
> behalf.
>
> **Applies To:** All external contributions or cross-repository patches where
> the author lacks direct commit/submit access.
>
> **Why:** External contributors regularly submitted valid fixes, but their
> changelists would stall indefinitely in the queue because they did not have
> the internal permissions required to trigger the final submission action.
> Failing to adhere to this typically results in **Stalled Integration / Review
> Timeout**.

**Trap 1: Expecting an external changelist to auto-submit without internal
administrative intervention.**

**Don't:**

*   Waiting indefinitely for a CI pipeline to merge an external contributor's CL
    after providing an LGTM.

**Do:**

*   The internal reviewer explicitly clicks the submit button or uses the
    appropriate proxy-merge command immediately after granting technical
    approval.

**Exceptions:** Repositories configured with strict auto-submit bots that do not
check individual committer permissions.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T4 | Identity Verification & Access Control Enforcement -
    *Dictates the external committer privileges and CLA status that determine
    whether a contributor requires proxy merging.*
*   **Downstream:** T8 | Review Pipeline State & Meta-Coordination - *External
    proxy operations directly dictate the changelist lifecycle and final
    submission state tracked within the review pipeline.*
