# InfoMentor for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![hassfest](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/hassfest.yml/badge.svg)](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/hassfest.yml)
[![Tests](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A first-class Home Assistant integration for **InfoMentor** (the Swedish school
platform, "Mentor" / "InfoMentor Hub"). It signs in with your normal
email/password account and turns your children's school day into sensors you can
build automations on.

> ⚠️ **Unofficial and not affiliated with InfoMentor.** For personal use with
> your own account. The API is undocumented and can change without warning —
> use at your own risk and respect the service's terms.

## What you get

One device per child, plus a hub device:

| Entity | Type | Meaning |
|---|---|---|
| `sensor.<child>_skoldag` | sensor | school day start–end (next school day, e.g. `08:00–14:40`), with the first/last lesson as attributes |
| `sensor.<child>_uppgifter` | sensor | number of assignments due within 7 days, with the list as an attribute |
| `sensor.<child>_nasta_handelse` | sensor | next calendar event (test, trip, …) |
| `binary_sensor.<child>_idrott_nasta_skoldag` | binary sensor | on when PE/gymnastics is coming up — *remember the gym bag* |
| `sensor.skolmat_nasta_skoldag` | sensor | school lunch (optional, from Mateo) |

Everything is polled on a configurable interval (default 20 minutes) using a
`DataUpdateCoordinator`, with **re-authentication** handled by Home Assistant.

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/c14ym0re/infomentor-homeassistant` as **Integration**
3. Install **InfoMentor** and restart Home Assistant
4. **Settings → Devices & Services → Add Integration → InfoMentor**

### Manually

Copy `custom_components/infomentor` into your HA `config/custom_components/`
folder and restart.

## Configuration

Enter the **email and password** of your InfoMentor (Mentor) account. The
integration signs in, discovers every child on the account, and creates the
entities above.

> Some municipalities use BankID/SSO instead of the email/password account. This
> integration uses the email/password flow; it depends on your school.

### Options

| Option | Default | Description |
|---|---|---|
| Update interval | 20 min | How often to poll (5–180). |
| Fetch school lunch | off | Download the menu from Mateo. |
| Mateo unit id | – | Numeric id from `meny.mateo.se/<municipality>/<id>`. |
| Nicknames | – | One per line: `Lastname, Firstname = Nickname`. |

## Automation ideas

```yaml
# Remind about the gym bag the evening before
automation:
  - alias: "Påminnelse idrott"
    triggers:
      - trigger: time
        at: "19:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.anna_idrott_nasta_skoldag
        state: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "Idrott imorgon – packa idrottskläderna!"

# Show the school day on a dashboard
type: entities
entities:
  - sensor.anna_skoldag
  - sensor.anna_uppgifter
  - sensor.anna_nasta_handelse
```

## How it works

The integration talks directly to `hub.infomentor.se` using `aiohttp` (bundled
with Home Assistant — no extra dependencies). Login follows the documented
email/password flow (`oauth_token` → mentor login form → credentials →
`isauthenticated`) and the session lives in cookies. Each poll logs in again, so
an expired session never matters.

All the endpoints were mapped and documented by the companion project
**[infomentor-api](https://github.com/c14ym0re/infomentor-api)** (CLI, email
reports, event alerts, MQTT bridge) — this integration is the Home Assistant
face of it.

## Troubleshooting

- **"Invalid email or password"** – check the credentials at
  <https://infomentor.se/swedish/production/mentor/>; some municipalities use SSO.
- **Re-authentication prompt** – the session was rejected; Home Assistant asks
  for the password again (nothing is logged out permanently).
- **No entities** – make sure the account actually has children with an active
  placement.

## Credits

- [kolplattformen/skolplattformen](https://github.com/kolplattformen/skolplattformen)
  (Apache-2.0) — endpoint documentation.
- [kolplattformen/dementor.net](https://github.com/kolplattformen/dementor.net)
  (MIT) — login flow.
- [c14ym0re/infomentor-api](https://github.com/c14ym0re/infomentor-api) — the
  companion toolkit and reference implementation.

## License

MIT © 2026 Claes Hall — see [LICENSE](LICENSE).
