# Trademark Filing Kit

This is an operational checklist for preparing a fast trademark filing for the
open source OpenRelix project. It is not legal advice.

Last reviewed: 2026-04-27.

## Fastest Practical Path

File the word mark first. Do not wait for a logo.

Recommended first filing:

- Jurisdiction: United States, if the project is distributed through GitHub,
  npm, or another channel that reaches U.S. users.
- Mark: `OPENRELIX`
- Drawing: Standard characters
- Owner: the individual or legal entity that will control the project brand
- Primary class: International Class 9
- Filing basis: Section 1(a) use in commerce if the downloadable project is
  already publicly available under the mark; otherwise Section 1(b) intent to
  use.

Add Class 42 only if there is a hosted web service, SaaS product, or online
non-downloadable software service under the same mark. The current preview repo is a
downloadable CLI / installer project, so Class 9 is the clean first filing.

Use `OPENRELIX` as the first protection target. Do not lead with an acronym or
nickname unless a trademark attorney clears it.

For China coverage, file `OPENRELIX` separately in China. A U.S. filing does
not automatically protect the mark there. See
`docs/china-chinese-trademark-filing-kit.md`.

## Goods And Services Draft

For USPTO filing, use the Trademark ID Manual inside Trademark Center whenever
possible. Selecting an acceptable ID Manual entry is usually faster and avoids
the extra free-form identification fee.

Class 9 draft to adapt through the ID Manual:

```text
Downloadable computer software for creating, organizing, storing, reviewing, and
displaying reusable workflow assets, namely skills, templates, automations, and
task reviews, for use with command-line artificial intelligence developer tools
```

If the Trademark Center offers a fill-in ID Manual entry, keep the structure from
the selected entry and fill only the function and field with the project-specific
wording. If the exact wording cannot fit an accepted ID Manual entry, expect a
higher official filing fee for free-form text.

Optional Class 42 draft only for a hosted service:

```text
Providing temporary use of online non-downloadable software for creating,
organizing, storing, reviewing, and displaying reusable workflow assets, namely
skills, templates, automations, and task reviews, for use with command-line
artificial intelligence developer tools
```

## Specimen Checklist

For a Section 1(a) use-in-commerce filing, capture a real public page that shows
the mark and the downloadable software together.

Best specimen options:

- npm package page showing `openrelix`, the project description, and an
  install command.
- GitHub README page showing `OpenRelix` and `npx openrelix
  install`.
- GitHub release or download page showing `OpenRelix` and a downloadable
  artifact or ZIP download.

The screenshot or webpage printout should include:

- The mark exactly as filed, ideally `OpenRelix`.
- A download, install, package, release, or repository action that makes the
  software available.
- The page URL and access date.
- Legible text without mockups or edited screenshots.

## Fast-Filing Data Sheet

Prepare these values before opening the filing form:

- Applicant legal name
- Applicant citizenship, if filing as an individual
- Applicant entity type and jurisdiction, if filing through a company
- Domicile address
- Public correspondence email
- Mark text: `OPENRELIX`
- Translation / transliteration: none
- Color claim: none for standard-character filing
- Prior registrations: none, unless you already own related marks
- First use anywhere date
- First use in U.S. commerce date
- Specimen screenshot file
- Goods / services class and ID Manual selection
- Owner signature name and title

## Open Source Release Boundary

Before publishing broadly:

- Keep `LICENSE` for source-code copyright permission.
- Keep `TRADEMARKS.md` for brand-name permission.
- Use `OpenRelix` consistently as the brand.
- Use `openrelix` consistently as the npm package name.
- Use `openrelix` consistently as the CLI command.
- Avoid saying the mark is registered until registration issues.
- Use `TM` while pending; use `R` in a circle only after registration and only in
  the registered jurisdiction.
