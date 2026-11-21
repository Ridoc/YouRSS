# YouRSS+ Auth, Account and Admin — DESIGN_SPEC

> Produced by `ux-designer` for BATCH_3/C2.I1 and reused by C3/C7. Tokens and
> classes are existing project components; this spec adds no new component.

```yaml
DESIGN_SPEC:
  SCOPE: "auth + account + admin + settings-dialog surfaces of YouRSS+; reuse existing tokens/classes only, no new components"
  PRINCIPLE: "one primary action per view; recovery action always visible next to the error; generic copy where enumeration is a risk"

DESIGN_SYSTEM:
  THEMES: ["data-theme=dark (default)", "data-theme=light", "data-theme=neon"]
  DENSITY: ["body.density-compact", "body.(default)", "body.density-large"]
  TOKENS: "consume as authored — --radius, --font, --bg, --bg-side, --bg-elev, --bg-hover, --bg-dialog, --fg, --fg-muted, --line, --line-strong, --accent, --accent-fg, --accent-soft, --ok, --amber, --err-bg, --err-line, --shadow"
  NEW_TOKENS: none
  COMPONENTS:
    .yourss-btn: "primary/secondary action; exactly one primary per view"
    .yourss-icon-btn: "icon-only, requires aria-label"
    .yourss-segment: "mutually exclusive view switch (Theme, Density)"
    .yourss-toggle + .yourss-switch: "boolean setting, aria-pressed is source of truth"
    .yourss-notice / .is-warning: "inline, non-blocking status line"
    .yourss-add-error: "inline field-level error, announced"
    .yourss-empty + .yourss-empty-action: "empty state + its single recovery action"
    .yourss-reset-btn: "destructive reset scoped to its section"
    .yourss-badge: "count/state label, never the only state carrier"
    .yourss-section: "titled content block inside account/admin/settings"
    .yourss-settings dialog/.yourss-settings-box: "settings surface, modal"
    .yourss-login: "centered card for /login and /auth/verify"
  SPACING: "keep existing scale; card padding inside .yourss-login/.yourss-section; gaps between .yourss-section blocks (existing rhythm); no ad-hoc insets"
  TYPE_SCALE: "existing body/heading scale; one heading per card; .yourss-empty copy = body size, muted; error text = body size, --fg on --err-bg"
  FOCUS_GLOBAL: "add :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px } — applies to links, buttons, inputs, summary, dialog controls; do not remove outline on :focus-within containers"

LAYOUT:
  /login:
    STRUCTURE: ".yourss-login card centered: h1 → subline → email field (label + autocomplete=email, inputmode=email) → Turnstile slot (when enabled, below field) → primary CTA → generic status region → legacy link (secondary, small)"
    RULE: "Turnstile slot reserved height to avoid CLS; status region above CTA never reflows the card"
  /auth/verify:
    STRUCTURE: ".yourss-login card: h1 → identity line (masked email) → confirm primary CTA → secondary 'Use a different email' → expired/invalid block replaces confirm area"
    RULE: "GET view is read-only — it does NOT consume the token; state it in copy"
  /account:
    STRUCTURE: "vertical stack of .yourss-section: Profile → Pages → Watched history → Sessions → Data (export/delete); page list rows with per-row actions right-aligned"
    RULE: "Danger zone = last section; delete page/account confirmations are dialogs, not inline replaces"
  /account/pages/<name>:
    STRUCTURE: "header (name + publish state .yourss-badge) → add-feed form (field + add CTA + .yourss-add-error) → ordered list (drag/collapse/reorder/remove) → footer actions (publish, warmup, empty-page reset)"
    RULE: "reorder affordances persistent (not hover-only) on coarse pointers; cap-reached renders .yourss-notice.is-warning above the add form and disables only the add CTA"
  /admin/users, /admin/status:
    STRUCTURE: "table/list of users (email, state .yourss-badge, actions) + status cards; filters in one row above the list"
    RULE: "destructive admin actions each confirmed; status view = read-only, no primary action"
  settings_dialog:
    STRUCTURE: ".yourss-settings-box: Appearance (theme .yourss-segment, density .yourss-segment) → Session (logout forms) → Close"
    RULE: "both logout forms stacked, distinct labels, each carries its own CSRF; legacy form visually secondary and placed last"

INTERACTION_MATRIX:
  /login:
    default: "email empty, CTA enabled; no error region rendered"
    loading: "CTA disabled + label 'Sending link…'; field read-only; status region reserved; no spinner-only feedback (text stays)"
    empty: "n/a — form is the empty state"
    error: "field error in .yourss-add-error linked via aria-describedby (invalid email); Turnstile failure → .yourss-notice.is-warning above CTA; rate-limited → same generic copy, no detail"
    success: "form replaced by .yourss-notice + generic sent message; link-back to /login"
    disabled: "CTA disabled while submitting or while Turnstile unresolved; disabled state must not rely on opacity alone (label states reason)"
  /auth/verify:
    default: "confirm CTA primary, email shown masked"
    loading: "CTA disabled + 'Signing in…'"
    empty: "n/a"
    error: "expired/invalid replaces confirm area: message + primary 'Request new link' (→/login)"
    success: "redirect into app; no intermediate screen"
    disabled: "account disabled → message + 'Back to sign in', no retry CTA"
  /account:
    default: "sections rendered; primary action = 'Edit pages' (or Save in profile)"
    loading: "section-level skeleton-free: keep layout, show 'Loading…' inline in the section body; do not collapse sections"
    empty: "pages: .yourss-empty + .yourss-empty-action primary; watched: empty line + 'Import watched history'"
    error: "section-scoped .yourss-notice.is-warning inside that section only; other sections stay usable"
    success: "inline .yourss-notice ('Saved.', 'Profile updated.'); auto-dismiss not required"
    disabled: "delete-account CTA disabled until typed confirmation matches; reason stated in one line"
  pages_editor:
    default: "add form active, ordered list, footer actions"
    loading: "row-level 'Working…' on the affected row only; reorder disabled while pending"
    empty: ".yourss-empty ('No feeds on this page yet.') + .yourss-empty-action 'Add a feed'"
    error: "duplicate → field-level .yourss-add-error; cap → .yourss-notice.is-warning + add CTA disabled; publish failure → row .yourss-notice.is-warning"
    success: "added row appears + .yourss-notice ('Added.'); publish → badge flips to published"
    disabled: "add CTA disabled at cap or blank field; remove/reorder disabled during pending mutation"
  admin:
    default: "list + filters; no primary action except the active filter's apply"
    loading: "list body keeps height; 'Loading…' in list region"
    empty: ".yourss-empty with 'No users match this filter.'"
    error: "page-level .yourss-notice.is-warning; list retains last data"
    success: "row action confirmed inline ('Disabled.', 'Enabled.')"
    disabled: "row actions disabled when self-targeted; tooltip/subline explains 'You cannot disable yourself'"
  settings_dialog:
    default: "open, focus trapped, Close primary-secondary"
    loading: "n/a (no async sync) — theme/density apply on selection"
    empty: "n/a"
    error: "CSRF/session error → .yourss-notice.is-warning inside dialog + reload link"
    success: "selection immediate; no save button (aria-pressed reflects)"
    disabled: "logout buttons disabled while submitting; both forms submit independently"

VISUAL_HIERARCHY:
  RULE: "1 h1 per view; primary CTA = only --accent-filled control; secondary = bordered; destructive = .yourss-reset-btn +/- --err-line"
  EMPHASIS: "state badges (.yourss-badge) never outrank the heading; errors use --err-bg/--err-line block, not colored text alone"
  READING_ORDER: "problem → cause → recovery action, in that DOM order"
  SCAN: "account/admin max 2 columns of content at wide; each section one idea"

RESPONSIVE:
  BREAKPOINTS: "use existing 899px / 559px / coarse-pointer; no new breakpoints"
  >899: "settings dialog = centered .yourss-settings-box; account sections stacked, single column (no sidebar duplication)"
  <=899: "dialog near-full width with existing insets; row actions wrap under the row content, right-aligned; tables become stacked rows with label/value pairs"
  <=559: "primary CTA full-width within card/section; email input full-width; legacy link below, still tappable; no horizontal scroll in lists"
  COARSE: "reorder/remove controls always visible (>=44px target); hover-only affordances are forbidden"

A11Y:
  CONTRAST: "only existing fg/bg pairs; states must not be conveyed by color alone (badge text + icon or word)"
  CONTRAST_CHECK: "OPEN_QUESTION for ux-reviewer: verify --fg-muted over --bg-elev, --accent-fg over --accent, and error text over --err-bg in all three themes"
  KEYBOARD: "full tab path login → verify → account → pages → admin → settings; settings dialog traps focus, Esc closes, focus returns to opener; reorder operable by keyboard (move up/down buttons), not drag-only"
  ARIA: "icon-only → aria-label; .yourss-toggle/.yourss-switch → aria-pressed; errors → role=alert on submit failure + aria-describedby on the field; status → role=status aria-live=polite; dialogs → role=dialog aria-modal + labelled heading; page list → list semantics with aria-label"
  FOCUS: "global :focus-visible as specified; visible focus on rows after mutation (return focus to the added row or its add field)"
  MOTION: "respect reduced-motion guards already present; no new animated transitions"
  FORMS: "every input has a persistent <label>; CSRF hidden field on all POST forms incl. both logout forms; autocomplete=email on login"

MICROCOPY:
  login_cta: "Send sign-in link"
  login_loading: "Sending link…"
  login_sent: "If that email is registered, a sign-in link is on its way. Check your inbox."
  login_invalid_email: "Enter a valid email address."
  login_rate_limited: "Too many requests. Wait a moment and try again."
  login_captcha_failed: "Verification failed. Try again."
  legacy_link_label: "Sign in with a legacy link"
  verify_title: "Confirm sign-in"
  confirm_sign_in: "Sign in as {email}?"
  verify_cta: "Sign in"
  verify_other_email: "Use a different email"
  expired_link: "That link has expired. Request a new one."
  invalid_link: "That link is not valid. Request a new one."
  request_new_link: "Request new link"
  disabled_account: "This account is disabled. Contact the admin."
  registration_closed: "Registration is closed. Ask the admin for an invite."
  empty_pages: "No pages yet."
  empty_pages_action: "Create a page"
  empty_feeds: "No feeds on this page yet."
  empty_feeds_action: "Add a feed"
  duplicate_subscription: "You're already subscribed to this feed."
  cap_reached: "Page limit reached. Remove a page to add another."
  delete_page_title: "Delete “{name}”?"
  delete_page_body: "This cannot be undone."
  delete_page_confirm: "Delete page"
  delete_page_cancel: "Keep page"
  delete_account_title: "Delete your account?"
  delete_account_body: "Your pages and watch history will be removed. This cannot be undone."
  delete_account_confirm: "Delete account"
  watched_import: "Import watched history"
  watched_import_success: "Watched history imported."
  watched_clear: "Clear watched history"
  watched_clear_confirm: "Clear all watched history?"
  logout_all: "Sign out everywhere"
  logout_all_confirm: "Sign out of all devices?"
  logout_session_label: "Sign out"
  logout_legacy_label: "Sign out legacy session"
  settings_title: "Settings"
  settings_close: "Close"
  admin_empty_users: "No users match this filter."
  admin_self_disable: "You cannot disable your own account."

REVIEW_CHECKS:
  - "OPEN_QUESTION for ux-reviewer: is the generic sent-message strong enough that users who mistyped still retry, without confirming registration?"
  - "OPEN_QUESTION for ux-reviewer: does /auth/verify make 'this does not sign you in yet' unambiguous before the CTA?"
  - "OPEN_QUESTION for ux-reviewer: are the two logout labels distinguishable without a tooltip in both placement contexts (dialog and account section)?"
  - "OPEN_QUESTION for ux-reviewer: at 559px, do stacked admin rows keep a usable action target for coarse pointer?"
  - "OPEN_QUESTION for ux-reviewer: is cap-reached feedback discoverable before the user types a feed?"
```
