# Arbeitszeit

Webbasierter Arbeitszeit-Rechner und Zeiterfassung (Python/FastAPI).

* **Zeitbuchungen** mit Projekt und Kommentar – wahlweise Von/Bis (+ Pause) oder reine Dauer (`1:30`, `1,5`, `90m`)
* **Soll/Ist-Rechnung** aus Jahresstunden (Standard 1700 h), Urlaubstagen (25), Feiertagen und Betriebsferien (24.–31.12.)
* **Ansichten**: Übersicht (Saldo, Jahresfortschritt, Ø nötige Stunden/Tag), Monat, Woche, Kalender, Jahr, Urlaub/Abwesenheit, Berichte (CSV-Export)
* **Monatsfreigabe**: Mitarbeiter reichen einen Monat ein → Freigeber gibt frei oder lehnt mit Begründung ab. Eingereichte/freigegebene Monate sind gesperrt.
* **Benutzerverwaltung** mit Rollen (Benutzer / Freigeber / Admin), lokale Anmeldung (bcrypt) **und** OIDC (Microsoft Entra ID, Google, beliebiger OIDC-Provider wie Keycloak/Okta/Authentik)
* Frontend: serverseitig gerendert (Jinja2) + [htmx](https://htmx.org) + [Pico CSS](https://picocss.com) – beides im Repo mitgeliefert, kein CDN, kein Build-Schritt, Dark-Mode (Umschalter in der Navigation; folgt standardmäßig der Systemeinstellung, Wahl wird im Browser gemerkt).

## Schnellstart

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # AZ_ADMIN_EMAIL / AZ_ADMIN_PASSWORD anpassen
arbeitszeit run --reload  # http://127.0.0.1:8000
pytest
```

Der erste Administrator wird beim ersten Start aus `AZ_ADMIN_EMAIL`/`AZ_ADMIN_PASSWORD` angelegt – alternativ `arbeitszeit create-admin you@example.com`.
Docker: `docker build -t arbeitszeit . && docker run -p 8000:8000 -v az:/data --env-file .env arbeitszeit`.

## Berechnung

* Arbeitstag = Mo–Fr ohne gesetzliche Feiertage (Bundesland pro Benutzer, Bibliothek `holidays`) und ohne Betriebsferien.
* **Tagessoll = Jahresstunden ÷ (Arbeitstage − Urlaubstage)**. Wer genau den Urlaubsanspruch nimmt, landet exakt bei den vertraglichen 1700 h.
  Sind die Urlaubstage dagegen in den 1700 h *enthalten*, `AZ_VACATION_IN_ANNUAL_HOURS=true` setzen (Tagessoll = Stunden ÷ Arbeitstage).
* Urlaub, Krank und sonstige Abwesenheit (auch halbe Tage) senken das Soll des Tages. Nur „Urlaub“ zehrt am Urlaubskonto.
* Saldo = Ist − Soll. Die Übersicht zeigt zusätzlich, wie viele Stunden pro verbleibendem Arbeitstag nötig sind, um das Jahresziel zu erreichen.
* **Einstieg mitten im Jahr / Wechsel vom alten Tool**: pro Benutzer *Startdatum* und *Startsaldo* (± Stunden) setzen (Admin → Benutzer). Das Soll zählt dann erst ab dem Startdatum.

## Freigabe-Workflow

`Offen → Eingereicht → Freigegeben` (oder `Abgelehnt → Offen`, mit Begründung). Freigeber sehen die Monate der ihnen zugeordneten Mitarbeiter (Admin → Benutzer → „Freigeber“); Admins sehen alle. Ein Freigeber kann einen freigegebenen Monat wieder öffnen. Bei Einreichung wird Ist/Soll als Momentaufnahme gespeichert.

## OIDC einrichten

Redirect-URI jeweils `{AZ_BASE_URL}/auth/<provider>/callback` mit `<provider>` = `azure`, `google` oder `sso` (generisch). Die nötigen Variablen stehen in `.env.example`.
Neue OIDC-Benutzer werden automatisch als Rolle „Benutzer“ angelegt (`AZ_OIDC_AUTO_CREATE_USERS=false` zum Abschalten). Bestehende lokale Konten werden nur per E-Mail verknüpft, wenn der Provider die Adresse als verifiziert meldet (bei Entra ID wird der Mandant als vertrauenswürdig behandelt). Mit `AZ_LOCAL_LOGIN_ENABLED=false` ist nur noch SSO möglich.

## Sicherheit

bcrypt-Passwörter, signierte Session-Cookies (`AZ_HTTPS_ONLY=true` hinter HTTPS), CSRF-Token auf allen POSTs, Login-Drosselung (pro Prozess), Open-Redirect-Schutz, CSV-Formel-Escaping, Rechteprüfung auf jeder Route.

## Aufbau

```
src/arbeitszeit/
  calc.py        reine Rechenlogik (Kalender, Soll/Ist, Urlaub)   ← gut getestet
  services.py    Fachlogik mit DB (Sperre, Freigabe, Abwesenheiten)
  routers/       auth, timesheet (Ansichten/Buchungen), approvals, reports, admin
  templates/     Jinja2 · static/ app.css + vendor (htmx, pico)
```

Schema wird beim Start per `create_all` angelegt (noch keine Migrationen – für spätere Schemaänderungen Alembic ergänzen).

## Grenzen

Feste 5-Tage-Woche (keine individuellen Arbeitstage/Teilzeitmuster), kein Überstunden-Übertrag zwischen Jahren, Login-Drosselung nur pro Prozess.
