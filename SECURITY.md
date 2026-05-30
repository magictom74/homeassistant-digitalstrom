# Security Policy

## Supported versions

This library is in alpha and the public API may change at any time.
Security fixes will land on the `main` branch and the most recent
released version. Older versions are not supported.

## Reporting a vulnerability

If you think you've found a security issue in `pydigitalstrom`,
**please do not open a public GitHub issue**. Instead, report it
privately via either:

- GitHub's [private security advisory mechanism](https://github.com/magictom74/ha-digitalstrom/security/advisories/new) (preferred)
- Email the maintainer (the email address in `pyproject.toml`)

You can expect:

1. An acknowledgement within ~7 days.
2. A short triage assessment with a severity estimate.
3. A fix on a private branch and a coordinated disclosure timeline.

## What counts as a security issue

Examples of things we'd want to know about privately first:

- A way to bypass the dSS auth model (e.g. session-token leakage
  in logs or error messages).
- A request the library makes that exposes the App-Token outside the
  documented `loginApplication` flow.
- A condition that lets a callable user trigger arbitrary system-state
  changes outside the documented public API.

Things that are not security issues for this project (please open a
normal issue instead):

- The dSS itself rejecting a request — that's a protocol bug.
- Mistakes in our threat model or documentation that don't actually
  let anyone do anything harmful.
- Crash / DoS bugs that only affect the caller's own process.
