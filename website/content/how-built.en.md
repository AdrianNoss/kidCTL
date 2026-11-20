---
title: "How kidCTL is built"
slug: "how-kidctl-is-built"
translationKey: "how-built"
description: "kidCTL is developed with AI and owned by a human – who does what and which rules apply."
---

KidCTL is developed by Adrian Noss **with the help of AI**. Most of the code, tests, translations and guides are
written by [Claude Code](https://claude.com/claude-code), an AI coding tool by Anthropic. A human is responsible for
kidCTL. This page explains how that fits together.

## Who does what

**Adrian** decides what kidCTL should and should not do. Every new feature is worked out beforehand through
questions and answers until the requirements are clear. Adrian tests on real devices with real games and launchers,
approves and publishes every release. Signing keys, servers and releases are in his hands only.

**Claude Code** implements what has been agreed: code, automated tests, translations, guides and screenshots. The AI
works only in the development environment with test data – it has no access to live servers, real family data or
keys.

## Fixed rules

- **Describe first, then build.** Every change is written into the specification
  ([`spec.md`](https://github.com/AdrianNoss/kidCTL/blob/main/spec.md)) first and recorded there with a date –
  together with what was tested.
- **No feature without tests.** Several hundred automated tests run before every release; new features come with
  their own tests.
- **System-level parts go to a test VM first.** Login, firewall and drive access are tried out in virtual machines
  (Arch Linux with KDE Plasma and GNOME, Windows 11), never untested on a real computer.
- **Small steps.** Larger plans are built in stages; each stage is its own traceable release.
- **Open rules.** The AI's working instructions are public in the repository
  ([`CLAUDE.md`](https://github.com/AdrianNoss/kidCTL/blob/main/CLAUDE.md)). Commits co-written by the AI are marked
  as such.
- **Automated checks.** CodeQL, OpenSSF Scorecard and a monthly scan of the public instance keep checking – the
  results are shown on the home page.

## What this means for you

- **kidCTL itself uses no AI.** The software sends nothing to AI services and analyses nothing with AI. AI is a tool
  for building it, not part of the product.
- **Your data stays with you.** The way it is built changes nothing about that: kidCTL only talks to your own server,
  without telemetry.
- **Everything can be checked.** The source code is free (AGPL-3.0); every change and its reason can be read in Git
  and in the specification.

## What does not exist yet

An **independent security audit by humans** has not taken place yet. Automated checks do not replace it. Known
limitations and ways around them are listed openly under [Limitations](../docs/limitations/). Please report security
issues privately: [Security](../docs/security/).

## Contributing

Contributions are welcome – including those made with AI assistance. The same rules apply: specification, tests,
docs in both languages. Whoever contributes stands behind their contribution.
