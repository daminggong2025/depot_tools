# Code Review Workflow Coordination Engineering Guide

## Executive Summary

Welcome to the Code Review Workflow Coordination Engineering Guide. This
authoritative repository of tribal knowledge exists to standardize our
engineering workflows, prevent the regression of historically prevalent failure
modes, and strictly enforce the procedural boundaries of the Android codebase.
By capturing the implicit rules of engagement for code reviews and
infrastructure management, this guide empowers incoming engineers to navigate
the development lifecycle with confidence and prevents stalled integration
pipelines.

The practices detailed herein span the entire lifecycle of a changelist—from
initial patchset scoping and targeted reviewer routing to recovering from merge
conflicts and invalidated approval states. We heavily emphasize explicit
asynchronous communication mechanisms to eliminate review stagnation, bypass
generalized queue delays, and prevent the diffusion of responsibility.
Furthermore, this guide enforces crucial operational standards, including
continuous integration configuration management, strict dependency versioning,
and low-level performance refactoring.

Engineers are expected to internalize these constraints to reduce the cognitive
load on peers, maintain healthy CI/CD pipelines, and eliminate the deadlocks
that traditionally plague asynchronous development. Use this index as your
primary reference for navigating systemic blockers and sustaining the high
velocity of our engineering ecosystem.

## Summary

| Chapter Theme / Title                | Scope & Objective                     |
| :----------------------------------- | :------------------------------------ |
| **Asynchronous Readiness Signaling** | The Asynchronous Readiness Signaling  |
:                                      : workflow relies on explicit shorthand :
:                                      : acronyms to trigger state transitions :
:                                      : in the review lifecycle. These        :
:                                      : signals ensure peers are notified     :
:                                      : immediately when a patchset is ready  :
:                                      : for initial or subsequent evaluation, :
:                                      : preventing review stagnation.         :
| **Targeted Reviewer Routing**        | Explicitly routing review requests to |
:                                      : specific domain experts using direct  :
:                                      : mentions ensures the correct          :
:                                      : stakeholder evaluates the change.     :
:                                      : This mechanism bypasses generalized   :
:                                      : queue delays and prevents the         :
:                                      : diffusion of responsibility inherent  :
:                                      : in broadcast review requests.         :
| **Post-Conflict Workflow             | This chapter outlines the protocols   |
: Resumption**                         : for explicitly signaling the          :
:                                      : resolution of administrative version  :
:                                      : control blockers, such as merge       :
:                                      : conflicts or rebases. Standardized    :
:                                      : notifications ensure reviewers resume :
:                                      : their evaluation cycle, preventing    :
:                                      : indefinite integration stalls.        :
| **Approval State Reset Recovery**    | This section governs the explicit     |
:                                      : signaling required when automated     :
:                                      : systems or incremental patchset       :
:                                      : updates invalidate previously granted :
:                                      : review approvals. Proper              :
:                                      : communication ensures reviewers       :
:                                      : restore stripped scores to prevent    :
:                                      : indefinite integration blockers.      :
| **Patchset Scope Qualification**     | This theme governs the strategic      |
:                                      : division and contextualization of     :
:                                      : changelists (CLs) to reduce reviewer  :
:                                      : cognitive load. It mandates breaking  :
:                                      : down monolithic updates into focused, :
:                                      : atomic patches and explicitly         :
:                                      : documenting the scope of partial or   :
:                                      : minimal changes to justify            :
:                                      : architectural fragmentation.          :
| **LUCI Configuration Management**    | This chapter dictates the strict      |
:                                      : configuration requirements for        :
:                                      : integrating repositories with LUCI    :
:                                      : CI/CD infrastructure. It mandates     :
:                                      : explicit access controls and project  :
:                                      : identification within `project.cfg`   :
:                                      : files to guarantee uninterrupted      :
:                                      : automated build pipelines.            :
| **Continuous Integration Feedback    | This chapter governs the              |
: Resolution**                         : communication protocols required when :
:                                      : resolving automated CI pipeline       :
:                                      : failures, modifying presubmit logic,  :
:                                      : or updating test suites. It ensures   :
:                                      : developers explicitly signal          :
:                                      : reviewers once broken tests are fixed :
:                                      : and the build is green to unblock the :
:                                      : review cycle.                         :
| **System Call & Performance          | This chapter governs the optimization |
: Refactoring**                        : of architectural execution by         :
:                                      : replacing high-overhead subprocess    :
:                                      : invocations with direct native system :
:                                      : calls. Strict adherence ensures       :
:                                      : maximum performance and eliminates    :
:                                      : metadata loss during file operations  :
:                                      : on targeted operating systems.        :
| **Environment & Dependency           | This section defines the constraints  |
: Versioning**                         : for updating environment              :
:                                      : configuration files and toolchain     :
:                                      : documentation. It ensures accurate    :
:                                      : synchronization of runtime            :
:                                      : dependencies and language versions    :
:                                      : alongside code changes to prevent     :
:                                      : localized execution failures.         :

--------------------------------------------------------------------------------
--------------------------------------------------------------------------------

## Chapter: Asynchronous Readiness Signaling

**Context:** The Asynchronous Readiness Signaling workflow relies on explicit
shorthand acronyms to trigger state transitions in the review lifecycle. These
signals ensure peers are notified immediately when a patchset is ready for
initial or subsequent evaluation, preventing review stagnation.

### Summary

| Rule ID   | Principle / Constraint   | Priority | Primary Symptom / Trap     |
| :-------- | :----------------------- | :------- | :------------------------- |
| **T1-01** | Explicit Asynchronous    | Medium   | Uploading a new patchset   |
:           : Readiness Signaling      :          : and assuming reviewers     :
:           :                          :          : will automatically know it :
:           :                          :          : is ready for inspection    :
:           :                          :          : based on the upload event  :
:           :                          :          : alone.                     :
| **T1-02** | Asynchronous Code Review | Medium   | Uploading a patchset       |
:           : Readiness Signaling      :          : without explicitly         :
:           :                          :          : notifying reviewers,       :
:           :                          :          : assuming they will         :
:           :                          :          : actively poll the code     :
:           :                          :          : review tool.               :
| **T1-03** | Asynchronous Patchset    | Medium   | Uploading a new patchset   |
:           : Readiness Signaling      :          : and implicitly expecting   :
:           :                          :          : reviewers to notice the    :
:           :                          :          : update without an explicit :
:           :                          :          : notification.              :

--------------------------------------------------------------------------------

### Rules

#### T1-01: Explicit Asynchronous Readiness Signaling

> **Rule:** Always use explicit shorthand acronyms to signal that a patchset is
> ready for peer evaluation.
>
> **What:** The standardized use of shorthand acronyms (like PTAL - Please Take
> A Look) to explicitly signal that a patchset has reached a state ready for
> initial or subsequent peer evaluation.
>
> **Applies To:** Patchset level communications across the code review
> lifecycle.
>
> **Why:** Without explicit communication signals, reviewers may not realize a
> newly uploaded patchset is ready for review or that previous feedback has been
> fully addressed, leading to stalled development cycles and idle changelists.
> Failing to adhere to this typically results in **Review Cycle Stagnation**.

**Trap 1: Uploading a new patchset and assuming reviewers will automatically
know it is ready for inspection based on the upload event alone.**

**Don't:**

*   Silently uploading a patchset without leaving a comment to trigger
    asynchronous notifications.

**Do:**

*   Explicitly commenting 'PTAL' or 'Please take a look' to signal readiness and
    trigger reviewer notifications.

**Exceptions:** Live synchronous pairing or situations where the reviewer is
actively watching the branch upload.

--------------------------------------------------------------------------------

#### T1-02: Asynchronous Code Review Readiness Signaling

> **Rule:** Explicitly trigger state transitions in the asynchronous review
> workflow using established shorthand notations.
>
> **What:** Using shorthand acronyms (e.g., PTAL - Please Take A Look) to
> explicitly trigger state transitions in the asynchronous review workflow,
> notifying peers that a patchset is ready for initial or subsequent evaluation.
>
> **Applies To:** Overall code review workflow and patchset lifecycle
> management.
>
> **Why:** Historically, code reviews stalled because reviewers were not
> explicitly notified when patchsets were successfully uploaded and ready for
> evaluation, leading to idle time in the asynchronous workflow. Failing to
> adhere to this typically results in **Review Stagnation**.

**Trap 1: Uploading a patchset without explicitly notifying reviewers, assuming
they will actively poll the code review tool.**

**Don't:**

*   Silently uploading a new patchset and waiting for reviewers to notice the
    update on their own.

**Do:**

*   Adding a patchset-level comment (e.g., 'PTAL') to explicitly trigger email
    notifications and signal readiness to the review team.

**Exceptions:** Automated bot uploads that handle their own lifecycle
notifications natively.

--------------------------------------------------------------------------------

#### T1-03: Asynchronous Patchset Readiness Signaling

> **Rule:** Never rely solely on automated system emails to notify peers of
> patchset readiness.
>
> **What:** Using standardized shorthand (e.g., 'PTAL') to explicitly notify
> peers that a patchset is ready for initial evaluation or re-evaluation.
>
> **Applies To:** Patchset-level workflow transitions in the code review
> lifecycle.
>
> **Why:** Uploading patchsets without explicit signaling causes changes to
> languish in a pending state, as reviewers are unaware the author has finished
> their modifications and addressed prior feedback. Failing to adhere to this
> typically results in **Review Stagnation**.

**Trap 1: Uploading a new patchset and implicitly expecting reviewers to notice
the update without an explicit notification.**

**Don't:**

*   Uploading a patchset and relying solely on automated Gerrit/system emails
    without confirming readiness.

**Do:**

*   Leaving an explicit patchset-level comment such as 'PTAL' (Please Take A
    Look) to trigger a workflow transition and confirm readiness.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Downstream:** T2 | Targeted Reviewer Routing - *Directing a readiness
    signal to a specific domain expert after triggering the workflow
    transition.*
*   **Downstream:** T4 | Approval State Reset Recovery - *Requiring subsequent
    asynchronous readiness signals when incremental updates invalidate previous
    approvals.*

## Chapter: Targeted Reviewer Routing

**Context:** Explicitly routing review requests to specific domain experts using
direct mentions ensures the correct stakeholder evaluates the change. This
mechanism bypasses generalized queue delays and prevents the diffusion of
responsibility inherent in broadcast review requests.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap    |
| :-------- | :------------------------ | :------- | :------------------------ |
| **T2-01** | Targeted Reviewer Routing | Medium   | Broadcasting a general    |
:           : via Explicit Mentions     :          : request for review when   :
:           :                           :          : specific domain expertise :
:           :                           :          : or formal ownership       :
:           :                           :          : approval is required.     :
| **T2-02** | Explicit Reviewer Routing | Medium   | Using a generic signal    |
:           :                           :          : when a specific domain    :
:           :                           :          : expert's approval is      :
:           :                           :          : actually required.        :
| **T2-03** | Explicit Reviewer         | Medium   | Using a generic readiness |
:           : Targeting and Routing     :          : signal when a specific    :
:           :                           :          : domain expert or code     :
:           :                           :          : owner is required to      :
:           :                           :          : unblock the review.       :
| **T2-04** | Targeted Reviewer Routing | Medium   | Broadcasting a review     |
:           :                           :          : readiness signal without  :
:           :                           :          : specifying the required   :
:           :                           :          : reviewer, causing         :
:           :                           :          : multiple assigned         :
:           :                           :          : reviewers to wait on each :
:           :                           :          : other.                    :

--------------------------------------------------------------------------------

### Rules

#### T2-01: Targeted Reviewer Routing via Explicit Mentions

> **Rule:** Always use explicit mentions to route review requests to the
> necessary domain experts or component owners.
>
> **What:** Directing review requests to specific domain experts or codebase
> owners using explicit mentions (e.g., @username) to ensure the correct
> stakeholder evaluates the change.
>
> **Applies To:** Code review platforms, specifically when assigning or routing
> feedback requests.
>
> **Why:** Generic review requests broadcasted to a large group can be
> overlooked due to the bystander effect, or reviewed by peers lacking the
> specific domain context required to safely approve structural changes. Failing
> to adhere to this typically results in **Unqualified Review / Unresponsive
> Reviewers**.

**Trap 1: Broadcasting a general request for review when specific domain
expertise or formal ownership approval is required.**

**Don't:**

*   Commenting 'PTAL' when a specific reviewer (e.g., the component owner) is
    actually needed for context or approval.

**Do:**

*   Directly addressing the intended reviewer alongside the readiness signal,
    such as 'Hi Yiwei, PTAL' or 'ptal estaab@'.

**Exceptions:** General style cleanups or trivial fixes where any available team
member possesses the necessary context to provide a valid approval.

--------------------------------------------------------------------------------

#### T2-02: Explicit Reviewer Routing

> **Rule:** Must tag the required reviewer on asynchronous readiness signals to
> bypass generalized queue delays and ensure domain-specific oversight.
>
> **What:** Asynchronous readiness signals should explicitly tag the required
> reviewer to bypass generalized queue delays and ensure domain-specific
> oversight.
>
> **Applies To:** Code review workflow, particularly for changes requiring
> explicit ownership approval or specific domain expertise.
>
> **Why:** Relying on generic review pools led to idle time and missed reviews,
> delaying necessary subsystem integrations as reviewers assumed others would
> pick up the ticket. Failing to adhere to this typically results in **Stalled
> Review Pipeline**.

**Trap 1: Using a generic signal when a specific domain expert's approval is
actually required.**

**Don't:**

*   Uploading a sensitive architectural change and dropping a generic "PTAL" in
    the thread without assigning reviewers.

**Do:**

*   Explicitly pinging the required approver in the comment thread (e.g.,
    "+reviewer_handle PTAL" or "reviewer_handle: ptal").

**Exceptions:** General code cleanups or unowned subsystems where any team
member holds sufficient context.

--------------------------------------------------------------------------------

#### T2-03: Explicit Reviewer Targeting and Routing

> **Rule:** Direct review requests to specific individuals using @-mentions or
> direct naming to prevent the diffusion of responsibility.
>
> **What:** Directing review requests to specific individuals using @-mentions
> or direct naming to ensure the correct domain expert evaluates the change and
> prevents the diffusion of responsibility.
>
> **Applies To:** Code review routing, especially in large codebases with strict
> ownership per subsystem.
>
> **Why:** Review requests that were broadcasted to a wide group often suffered
> from the bystander effect, where no specific expert took ownership of the
> review, delaying the integration. Failing to adhere to this typically results
> in **Diffused Responsibility**.

**Trap 1: Using a generic readiness signal when a specific domain expert or code
owner is required to unblock the review.**

**Don't:**

*   Posting a generic 'PTAL' on a change list with multiple reviewers, leaving
    it ambiguous as to who should provide the primary approval.

**Do:**

*   Directly addressing the required reviewer (e.g., '@[Reviewer Email] PTAL' or
    '[Reviewer Name], PTAL') to ensure explicit routing.

**Exceptions:** Initial broad sweeps where anyone on a team mailing list is
equally qualified to do the first pass.

--------------------------------------------------------------------------------

#### T2-04: Targeted Reviewer Routing

> **Rule:** Never broadcast readiness signals without specifying the required
> stakeholder when waiting on specific architectural validation.
>
> **What:** Explicitly directing a review request to a specific individual or
> stakeholder to ensure the correct domain expert evaluates the change.
>
> **Applies To:** Patchset-level comments when specific domain expertise or a
> follow-up from a specific individual is required.
>
> **Why:** Relying on generic broadcast notifications leads to a diffusion of
> responsibility, delaying critical reviews because multiple assigned reviewers
> assume someone else will handle it. Failing to adhere to this typically
> results in **Diffusion of Responsibility**.

**Trap 1: Broadcasting a review readiness signal without specifying the required
reviewer, causing multiple assigned reviewers to wait on each other.**

**Don't:**

*   Leaving a generic 'PTAL' comment when waiting on a specific architectural
    approval or specific feedback validation.

**Do:**

*   Directing the request to the required expert using mentions, e.g., 'PTAL
    @[reviewer-email]' or '[Reviewer Name], PTAL.'

**Exceptions:** Initial reviews on small, non-critical components where any
member of the reviewer pool can safely evaluate and provide approval.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T1 | Asynchronous Readiness Signaling - *Provides the baseline
    asynchronous notification mechanisms (such as "PTAL") that targeted reviewer
    routing modifies to bypass queue delays.*

## Chapter: Post-Conflict Workflow Resumption

**Context:** This chapter outlines the protocols for explicitly signaling the
resolution of administrative version control blockers, such as merge conflicts
or rebases. Standardized notifications ensure reviewers resume their evaluation
cycle, preventing indefinite integration stalls.

### Summary

| Rule ID   | Principle / Constraint | Priority | Primary Symptom / Trap       |
| :-------- | :--------------------- | :------- | :--------------------------- |
| **T3-01** | Post-Conflict Workflow | Medium   | Rebasing a branch to resolve |
:           : Resumption Signaling   :          : a conflict but failing to    :
:           :                        :          : notify the reviewers that    :
:           :                        :          : the blocker has been         :
:           :                        :          : removed.                     :

--------------------------------------------------------------------------------

### Rules

#### T3-01: Post-Conflict Workflow Resumption Signaling

> **Rule:** Always explicitly notify reviewers upon resolving merge conflicts to
> proactively unblock and resume the paused review cycle.
>
> **What:** The process of explicitly signaling to reviewers that administrative
> version control blockers, such as merge conflicts, have been resolved via a
> rebase.
>
> **Applies To:** Version control and merge strategy workflows, specifically
> when resolving git conflicts.
>
> **Why:** Reviewers often pause their evaluation when a patchset develops a
> merge conflict. Failing to explicitly notify them when the conflict is
> resolved leaves the patchset in an indefinitely blocked state from the
> reviewer's perspective. Failing to adhere to this typically results in
> **Stalled Integration / Deadlocked Review**.

**Trap 1: Rebasing a branch to resolve a conflict but failing to notify the
reviewers that the blocker has been removed.**

**Don't:**

*   Force-pushing a conflict resolution without leaving a status update comment
    to re-engage the reviewer.

**Do:**

*   Leaving a comment explicitly stating the conflict was resolved: 'Had to
    rebase because of a conflict. PTAL once more?'

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T1 | Asynchronous Readiness Signaling - *Post-conflict
    resumption relies on standard readiness acronyms (e.g., PTAL) to formally
    trigger state transitions in the review workflow.*
*   **Downstream:** T4 | Approval State Reset Recovery - *Force-pushing a rebase
    to clear a conflict often clears previous reviewer scores, requiring an
    explicit approval state reset workflow.*

## Chapter: Approval State Reset Recovery

**Context:** This section governs the explicit signaling required when automated
systems or incremental patchset updates invalidate previously granted review
approvals. Proper communication ensures reviewers restore stripped scores to
prevent indefinite integration blockers.

### Summary

| Rule ID   | Principle / Constraint | Priority | Primary Symptom / Trap       |
| :-------- | :--------------------- | :------- | :--------------------------- |
| **T4-01** | Approval State Reset   | Medium   | Assuming a previous approval |
:           : Recovery Signaling     :          : carries over to a newly      :
:           :                        :          : uploaded patchset without    :
:           :                        :          : verifying the system's state :
:           :                        :          : or the automation rules.     :
| **T4-02** | Explicit Recovery of   | High     | Waiting for a merge after an |
:           : Invalidated Review     :          : incremental upload drops the :
:           : Approvals              :          : necessary approval scores,   :
:           :                        :          : unaware that reviewers need  :
:           :                        :          : to actively re-approve.      :

--------------------------------------------------------------------------------

### Rules

#### T4-01: Approval State Reset Recovery Signaling

> **Rule:** Always explicitly notify reviewers to restore their approval scores
> after uploading new patchsets that invalidate prior approvals.
>
> **What:** Proactively requesting a subsequent review pass when automated
> systems strip a previously granted approval score (+1) due to incremental
> patchset updates.
>
> **Applies To:** Gerrit or similar code review systems with strict
> patchset-level scoring policies.
>
> **Why:** When a developer addresses a minor nit after receiving an approval,
> the system may automatically revoke the approval score. The reviewer must be
> explicitly summoned back to restore their score, otherwise the pipeline is
> blocked. Failing to adhere to this typically results in **Approval Score
> Invalidation**.

**Trap 1: Assuming a previous approval carries over to a newly uploaded patchset
without verifying the system's state or the automation rules.**

**Don't:**

*   Uploading a minor fix to address a comment and expecting the PR to merge
    automatically, unaware that the +1 was dropped.

**Do:**

*   Explicitly pinging the reviewer to restore the score: 'PTAL again as
    addressing comments invalidated prior +1.'

**Exceptions:** Platforms or specific repository configurations where approvals
are sticky across non-conflicting patchsets.

--------------------------------------------------------------------------------

#### T4-02: Explicit Recovery of Invalidated Review Approvals

> **Rule:** Must explicitly communicate workflow state changes to reviewers
> whenever an incremental upload or rebase drops a previously granted score.
>
> **What:** Requesting a subsequent review pass when automated systems or
> incremental patchset uploads strip a previously granted approval score (e.g.,
> a +1 or LGTM).
>
> **Applies To:** Code review platforms (e.g., Gerrit) where patchset uploads
> automatically drop previous reviewer scores.
>
> **Why:** Incremental uploads or rebases frequently dropped previous reviewer
> approvals. Without explicitly noting this state reset, CLs became indefinitely
> blocked because reviewers assumed their previous approval was still valid and
> active. Failing to adhere to this typically results in **Blocked
> Integration**.

**Trap 1: Waiting for a merge after an incremental upload drops the necessary
approval scores, unaware that reviewers need to actively re-approve.**

**Don't:**

*   Uploading a minor semantic change or rebase that drops a '+1' score, then
    leaving the review idle without notifying the approver.

**Do:**

*   Explicitly stating the workflow state change to reviewers in the comment,
    e.g., 'PTAL again, the +1 was removed'.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T3 | Post-Conflict Workflow Resumption - *Resolving merge
    conflicts and rebasing often triggers the patchset score invalidations that
    require recovery signaling.*
*   **Downstream:** T1 | Asynchronous Readiness Signaling - *Summoning reviewers
    to restore dropped approvals relies on standard readiness shorthands like
    'PTAL'.*

## Chapter: Patchset Scope Qualification

**Context:** This theme governs the strategic division and contextualization of
changelists (CLs) to reduce reviewer cognitive load. It mandates breaking down
monolithic updates into focused, atomic patches and explicitly documenting the
scope of partial or minimal changes to justify architectural fragmentation.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap   |
| :-------- | :------------------------ | :------- | :----------------------- |
| **T5-01** | Atomic Patchset Scoping   | Medium   | Submitting a single      |
:           :                           :          : massive CL containing    :
:           :                           :          : formatting, refactoring, :
:           :                           :          : and feature work.        :
| **T5-02** | Proactive Scope           | Medium   | Submitting a fragment of |
:           : Qualification for Partial :          : a larger feature without :
:           : Patchsets                 :          : explaining its context,  :
:           :                           :          : causing reviewers to     :
:           :                           :          : question missing logic.  :

--------------------------------------------------------------------------------

### Rules

#### T5-01: Atomic Patchset Scoping

> **Rule:** Never submit monolithic updates; always fragment large architectural
> refactors into atomic, easily reviewable changelists.
>
> **What:** Break down large architectural refactors into small, distinct, and
> focused changelists (CLs) rather than submitting monolithic updates.
>
> **Applies To:** Broad software development process and code review
> submissions.
>
> **Why:** Monolithic CLs previously increased cognitive load on reviewers,
> obscuring logic flaws behind superficial formatting changes and leading to
> slower, less accurate review cycles. Failing to adhere to this typically
> results in **Review Fatigue / Blocked Pipeline**.

**Trap 1: Submitting a single massive CL containing formatting, refactoring, and
feature work.**

**Don't:**

*   Submitting a 2,000-line changelist that refactors core logic, updates tests,
    and fixes unrelated bugs simultaneously.

**Do:**

*   Submitting 4 separate, focused 500-line changelists that can be individually
    reasoned about and tested in isolation.

**Exceptions:** Automated, repository-wide mechanical refactors (e.g., global
rename) where chunking provides no analytical benefit.

--------------------------------------------------------------------------------

#### T5-02: Proactive Scope Qualification for Partial Patchsets

> **Rule:** Must proactively explain the scope and context of fragmented or
> incomplete changelists to prevent reviewer confusion.
>
> **What:** Providing proactive explanations regarding the size, structure, or
> focus of a changelist to reduce cognitive load and justify architectural
> fragmentation to reviewers.
>
> **Applies To:** Incremental development workflows, split change lists, and
> minor hotfixes.
>
> **Why:** Reviewers frequently pushed back on patchsets that seemed incomplete
> or overly simplistic because they lacked the context that the change was
> either a targeted hotfix or part of a planned, larger sequence of commits.
> Failing to adhere to this typically results in **Reviewer Confusion**.

**Trap 1: Submitting a fragment of a larger feature without explaining its
context, causing reviewers to question missing logic.**

**Don't:**

*   Submitting a partial feature CL with just a standard review request,
    prompting reviewers to ask 'where is the rest of the implementation?'

**Do:**

*   Contextualizing the chunk explicitly in the review request: 'PTAL. This is
    really part of a bigger CL but I did my best to split it up into manageable
    chunks.'

**Trap 2: Submitting a tiny fix without scoping it, causing reviewers to
over-analyze for hidden complexity.**

**Don't:**

*   Requesting a review for a 1-line change without characterizing its risk or
    scope.

**Do:**

*   Explicitly scoping the review as minimal: 'PTAL small fix'.

**Exceptions:** Self-contained, standard-sized CLs that require no additional
scoping context.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Downstream:** T1 | Asynchronous Readiness Signaling - *Scope qualification
    often modifies or accompanies standard asynchronous readiness signals (e.g.,
    "PTAL small fix") to set immediate context for the evaluation.*

## Chapter: LUCI Configuration Management

**Context:** This chapter dictates the strict configuration requirements for
integrating repositories with LUCI CI/CD infrastructure. It mandates explicit
access controls and project identification within `project.cfg` files to
guarantee uninterrupted automated build pipelines.

### Summary

| Rule ID   | Principle / Constraint    | Priority | Primary Symptom / Trap   |
| :-------- | :------------------------ | :------- | :----------------------- |
| **T6-01** | LUCI Project              | Critical | Removing or omitting the |
:           : Identification and Access :          : LUCI project             :
:           : Definition                :          : configuration file,      :
:           :                           :          : causing build bots to    :
:           :                           :          : fail access checks.      :

--------------------------------------------------------------------------------

### Rules

#### T6-01: LUCI Project Identification and Access Definition

> **Rule:** Always maintain a strictly defined `project.cfg` file at the
> repository root to authorize LUCI CI/CD integration.
>
> **What:** Repositories integrated with LUCI infrastructure must maintain a
> `project.cfg` file at the root defining the project name and baseline access
> controls.
>
> **Applies To:** CI/CD infrastructure integration; specifically `project.cfg`
> configuration files.
>
> **Why:** The omission or deletion of `project.cfg` severed the repository's
> integration with LUCI CI/CD infrastructure, preventing build ecosystem
> services from verifying access permissions and halting automated pipelines.
> Failing to adhere to this typically results in **CI/CD Pipeline
> Disconnection**.

**Trap 1: Removing or omitting the LUCI project configuration file, causing
build bots to fail access checks.**

**Don't:**

```text
// BAD: Deleting project.cfg or leaving it empty
```

**Do:**

```text
# GOOD: Explicitly define schema access and project name in project.cfg
name: "depot_tools"
access: "group:all" # public
```

## Chapter: Continuous Integration Feedback Resolution

**Context:** This chapter governs the communication protocols required when
resolving automated CI pipeline failures, modifying presubmit logic, or updating
test suites. It ensures developers explicitly signal reviewers once broken tests
are fixed and the build is green to unblock the review cycle.

### Summary

| Rule ID   | Principle / Constraint | Priority | Primary Symptom / Trap     |
| :-------- | :--------------------- | :------- | :------------------------- |
| **T8-01** | Continuous Integration | Medium   | Fixing a failing test or   |
:           : State Update Signaling :          : completing manual testing  :
:           :                        :          : but failing to alert the   :
:           :                        :          : reviewer that the pipeline :
:           :                        :          : is now clear.              :

--------------------------------------------------------------------------------

### Rules

#### T8-01: Continuous Integration State Update Signaling

> **Rule:** Always explicitly notify reviewers when failing CI pipelines, broken
> tests, or presubmit checks have been resolved. Never rely on automated
> dashboard updates to re-engage reviewers.
>
> **What:** Notifying reviewers when automated test suites, presubmit logic, or
> manual verification steps have been completed, unblocking the review process.
>
> **Applies To:** CI/CD integration checkpoints, automated presubmit hooks, and
> testing validation phases.
>
> **Why:** Reviewers will typically halt their code review if CI checks (tests)
> are actively failing. Once fixed, an explicit signal is needed to draw their
> attention back to the green build. Failing to adhere to this typically results
> in **Abandoned Review Pass**.

**Trap 1: Fixing a failing test or completing manual testing but failing to
alert the reviewer that the pipeline is now clear.**

**Don't:**

*   Silently pushing a test fix and waiting indefinitely for the reviewer to
    notice the green checkmark on the CI pipeline.

**Do:**

*   Adding a comment to summarize the state of the tests and requesting a
    re-review: 'test updated. PTAL' or 'PTAL - will manually verify shortly'.

--------------------------------------------------------------------------------

### Cross-Domain Dependencies

*   **Upstream:** T1 | Asynchronous Readiness Signaling - *Provides the
    standardized asynchronous triggers (e.g., PTAL) utilized to notify peers
    after CI pipeline clearance.*
*   **Downstream:** T4 | Approval State Reset Recovery - *Submitting incremental
    test fixes to satisfy CI pipelines frequently invalidates previously granted
    approval scores, requiring a targeted re-review.*

## Chapter: System Call & Performance Refactoring

**Context:** This chapter governs the optimization of architectural execution by
replacing high-overhead subprocess invocations with direct native system calls.
Strict adherence ensures maximum performance and eliminates metadata loss during
file operations on targeted operating systems.

### Summary

| Rule ID    | Principle / Constraint | Priority | Primary Symptom / Trap   |
| :--------- | :--------------------- | :------- | :----------------------- |
| **T10-01** | Low-level System Calls | High     | Spawning a new shell     |
:            : for macOS File         :          : process to perform basic :
:            : Operations             :          : file operations.         :

--------------------------------------------------------------------------------

### Rules

#### T10-01: Low-level System Calls for macOS File Operations

> **Rule:** Always use native `clonefile` system calls via `ctypes` instead of
> spawning subprocesses for file operations on macOS to eliminate execution
> overhead.
>
> **What:** Transition from high-level `subprocess` invocations to native
> `clonefile` system calls via `ctypes` on macOS to eliminate process-spawning
> overhead and preserve metadata.
>
> **Applies To:** macOS specific Python scripts and utilities handling file
> copying or metadata duplication within the build environment.
>
> **Why:** Historically, invoking subshells via `subprocess` for simple file
> operations caused massive execution overhead, extending workdir creation times
> on macOS from seconds to minutes. Failing to adhere to this typically results
> in **Performance Degradation / Timeout**.

**Trap 1: Spawning a new shell process to perform basic file operations.**

**Don't:**

```python
# BAD: High overhead from spawning a subprocess shell
subprocess.run(['cp', '-p', src, dest])
```

**Do:**

```python
# GOOD: Direct native system call with metadata flags
import ctypes

CLONE_NOFOLLOW = 0x0001
CLONE_ACL = 0x0002

libc = ctypes.CDLL(None)
libc.clonefile(src.encode('utf-8'), dest.encode('utf-8'), CLONE_NOFOLLOW | CLONE_ACL)
```

**Exceptions:** Cross-platform code paths where `clonefile` is unavailable
(e.g., Linux, Windows) must fallback to standard library implementations or
subprocesses.

## Chapter: Environment & Dependency Versioning

**Context:** This section defines the constraints for updating environment
configuration files and toolchain documentation. It ensures accurate
synchronization of runtime dependencies and language versions alongside code
changes to prevent localized execution failures.

### Summary

| Rule ID    | Principle / Constraint | Priority | Primary Symptom / Trap      |
| :--------- | :--------------------- | :------- | :-------------------------- |
| **T11-01** | Runtime Dependency     | High     | Bumping a script's runtime  |
:            : Documentation          :          : version requirement without :
:            : Synchronization        :          : updating the associated     :
:            :                        :          : developer documentation.    :

--------------------------------------------------------------------------------

### Rules

#### T11-01: Runtime Dependency Documentation Synchronization

> **Rule:** Always synchronize documentation files in the exact same changelist
> when altering runtime dependencies or required language versions.
>
> **What:** Documentation (such as README files) must be explicitly synchronized
> alongside any code changes that alter runtime dependencies or required
> language versions.
>
> **Applies To:** Repository root documentation, environment configuration, and
> toolchain definitions.
>
> **Why:** Toolchains failed to execute locally for developers because the
> required Python version was updated in the codebase but the corresponding
> environment documentation was left outdated, leading to confusing local
> failures. Failing to adhere to this typically results in **Local Environment
> Failure**.

**Trap 1: Bumping a script's runtime version requirement without updating the
associated developer documentation.**

**Don't:**

*   Updating a python script to use Python 3.10+ syntax while the README states
    Python 3.6 is supported.

**Do:**

*   Updating the Python script and synchronously committing an update to the
    README and requirement configuration files in the same changelist.
