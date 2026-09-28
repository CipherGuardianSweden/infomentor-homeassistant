# Så funkar InfoMentor i Home Assistant

En kort guide med copy‑paste‑färdiga exempel. Integrationen är inofficiell och
inte kopplad till InfoMentor.

> **Hitta dina entitets-ID:n:** Inställningar → Enheter & tjänster → **InfoMentor**
> → öppna en enhet → *Entiteter*. ID:na byggs från barnets namn (och de
> smeknamn du sätter i integrationens inställningar), t.ex.
> `sensor.anna_school_day`. Byt ut namnen i exemplen mot dina egna.

## Vad du får

Ett **device per barn** med:

| Entitet | Visar |
|---|---|
| `…_school_day` | skoldagens start–slut nästa skoldag, t.ex. `08:00–14:40` |
| `…_assignments_due` | antal uppgifter som förfaller inom 7 dagar (listan finns som attribut) |
| `…_next_event` | nästa kalenderhändelse (prov, utflykt …) |
| `…_pe_next_school_day` | **på** när idrott/gymnastik väntar nästa skoldag |

Plus ett nav‑device med `…_lunch` om du aktiverat skolmat.

---

## 1. Påminnelse om idrottskläderna 🏃

Lampan/påminnelsen som gör att ingen glömmer gympapåsen:

```yaml
alias: "Idrott imorgon – packa väskan"
triggers:
  - trigger: time
    at: "19:00"
conditions:
  - condition: state
    entity_id: binary_sensor.anna_pe_next_school_day
    state: "on"
actions:
  - action: notify.mobile_app_din_telefon
    data:
      title: "🏃 Idrott imorgon"
      message: "Glöm inte idrottskläderna!"
mode: single
```

Vill du fånga alla barn i en automation, gör tre conditions/actions eller kör
`state` per sensor.

## 2. Morgonöversikt ☀️

Skickar en kort sammanfattning innan skolan börjar:

```yaml
alias: "Morgonöversikt – skolan"
triggers:
  - trigger: time
    at: "07:00"
actions:
  - action: notify.mobile_app_din_telefon
    data:
      title: "🎒 Skoldagen idag"
      message: >-
        {% set barn = {
          'Anna': ('sensor.anna_school_day', 'sensor.anna_assignments_due'),
          'Bo':    ('sensor.bo_school_day',    'sensor.bo_assignments_due'),
          'Lisa': ('sensor.lisa_school_day', 'sensor.lisa_assignments_due')
        } %}
        {% for namn, s in barn.items() -%}
        {{ namn }}: {{ states(s[0]) }} · {{ states(s[1]) }} uppgifter
        {% endfor %}
mode: single
```

## 3. Larma när ett prov dyker upp 📅

Reagerar när "nästa händelse" ändras (t.ex. ett nytt prov läggs in):

```yaml
alias: "Nytt i skolans kalender"
triggers:
  - trigger: state
    entity_id: sensor.anna_next_event
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.state not in ['unknown', 'unavailable', 'None'] }}"
actions:
  - action: notify.mobile_app_din_telefon
    data:
      message: "Nytt i kalendern: {{ trigger.to_state.state }} ({{ trigger.to_state.attributes.date }})"
mode: single
```

## 4. Skolpanel i hallen 🖥️

Ett enkelt kort som visar läget (byt entitets-ID:n mot dina):

```yaml
type: entities
title: Skolan
entities:
  - entity: sensor.anna_school_day
    name: Anna – skoldag
  - entity: sensor.anna_assignments_due
    name: Anna – uppgifter kvar
  - entity: binary_sensor.anna_pe_next_school_day
    name: Idrott imorgon
  - entity: sensor.anna_next_event
    name: Nästa händelse
```

## Skolmat 🍽️

Aktivera **Hämta skolmat** i inställningarna och ange **Mateo‑enhets‑ID** från
länken på `meny.mateo.se` (den sista siffran i adressen, t.ex. `…/kommun/123` →
`123`). Då får du nästa skoldags rätter i en sensor.

---

## Vanliga frågor

**Kräver det BankID?**
Nej — integrationen loggar in med det vanliga **e‑post/lösenordskontot**
(Mentor). Vissa kommuner använder BankID/SSO; då fungerar den inte (än).

**Var förvaras uppgifterna?**
Lokalt i Home Assistant. Inloggningsuppgifterna används bara för att logga in
mot InfoMentor och skickas ingen annanstans.

**Är den i HACS-butiken?**
Inte än — den är precis släppt och körs först hemma hos oss. Installera som
**Custom repository** (kategori: Integration) tills vidare.

**Något strular?**
Öppna en issue på GitHub med vad du ser i loggen
(Inställningar → System → Loggar, filtrera på `infomentor`) — feedback är guld
under inkörningen!
