# AgentConfig

**TL;DR** — Installiert deine geprüften Engineering-Regeln und manuell aufrufbaren Skills zentral für den VS-Code-Chat mit lokal angebundenem Qwen. Die Projekt-Repositories brauchen dafür keine Dateien. Vorschau mit `plan`, Installation mit `apply`, Rückkehr zum vorigen Stand mit `rollback`.

## Einrichten

Voraussetzungen sind Git und Python 3.10+ in der Umgebung, deren Benutzerverzeichnis VS Code für Skills und Instructions durchsucht. Die drei Repositories einmal nebeneinander klonen. Bereits vorhandene Checkouts kannst du weiterverwenden.

```sh
git clone https://github.com/ghinta/Rules.git
git clone https://github.com/ghinta/Skills.git
git clone https://github.com/ghinta/AgentConfig.git
cd AgentConfig
```

| Umgebung | Vorschau | Anwenden |
|---|---|---|
| Windows / PowerShell | `py -3 agentconfig.py plan` | `py -3 agentconfig.py apply` |
| WSL / Linux | `python3 agentconfig.py plan` | `python3 agentconfig.py apply` |

Mit `--sources VERZEICHNIS` wählst du das gemeinsame Elternverzeichnis der beiden Quell-Checkouts. WSL kann dieselben Windows-Checkouts beispielsweise mit `--sources "/mnt/c/Users/NAME/AgentRepos"` lesen. Mit `--home VERZEICHNIS` wählst du ein bereits vorhandenes, separates Ziel zum Ausprobieren. Ohne diese Option gilt das Home des ausführenden Benutzers.

## Was VS Code lädt

| Quelle | Ziel relativ zum Benutzerverzeichnis | Aktivierung |
|---|---|---|
| Nummerierte Markdown-Dateien aus Rules | `.copilot/instructions/ghinta/*.instructions.md` | Basis und Aufrufpolitik mit `applyTo: "**"`, Fachregeln nach Dateimuster oder Aufgabenbeschreibung |
| `Skills/.agents/skills/<name>/` einschließlich lokaler Referenzen und Vorlagen | `.copilot/skills/<name>/` | Explizit über `/<name>`; die Installationskopie erhält `disable-model-invocation: true` |

Die Python-Regel legt ihre Dateimuster mit `applyTo` direkt in `Rules/20-python-framework.md` fest. AgentConfig übernimmt sie mitsamt Frontmatter. Für Regeln ohne Frontmatter liefert [vscode-local.json](vscode-local.json) den Auslöser. In Local-Sitzungen kann eine Aufgabenbeschreibung unabhängig vom Dateimuster zur Auswahl führen. Welche Dateien einen konkreten Chat erreicht haben, zeigt die Instruction-Diagnose.

Für aktuelle Standardkonfigurationen erkennt VS Code beide Zielordner. Falls dein Profil andere Suchorte definiert, diese Einträge in den **User Settings** ergänzen und vorhandene Einträge beibehalten:

```jsonc
{
  "chat.instructionsFilesLocations": {
    "~/.copilot/instructions": true
  },
  "chat.includeApplyingInstructions": true
}
```

Zielprofil ist das Session Target **Local**. Im Modellmenü bleibt dein bereits konfigurierter Qwen-Endpunkt ausgewählt. Es gibt keine Installation auf dem reinen Inferenzserver und keine Änderung der Provider-Einstellungen. Andere Harnesses, die dieselben Benutzerordner lesen, können die installierten Dateien ebenfalls entdecken.

## Geprüfte Updates

1. Neue Revisionen bei Bedarf selbst holen. Aus dem AgentConfig-Verzeichnis: `git -C ../Rules fetch origin` und `git -C ../Skills fetch origin`.
2. `python3 agentconfig.py plan --candidate` vergleicht beide lokal verfügbaren `origin/main`-Stände mit der Installation und zeigt die Commit-IDs. Unter Windows jeweils `py -3` verwenden.
3. Den Inhalt beider Git-Diffs zwischen bisherigem Pin und Kandidat prüfen, beispielsweise mit `git -C ../Skills diff ALTER_SHA NEUER_SHA`. Die Skill-Validierung der Quellrevision und die zugehörige CI gehören zum Review. Freigegebene vollständige SHAs in [sources.lock.json](sources.lock.json) übernehmen. So ist derselbe Stand auf allen Rechnern reproduzierbar.
4. `plan` zeigt die betroffenen Zielpfade, `apply` installiert die gepinnten Git-Blobs. Uncommittete Quelländerungen fließen nicht ein. Beide Befehle arbeiten mit lokalen Git-Objekten.

| Änderung | Ergebnis |
|---|---|
| Neuer, geänderter oder entfernter Skill | Beim nächsten freigegebenen Update wird der gesamte Skill-Ordner abgeglichen. Der fachliche Katalog in Rules bleibt bei den Autoren. |
| Geänderte oder entfernte Fachregel | AgentConfig ersetzt oder entfernt die zugehörige generierte Datei. |
| Neue Fachregel | Mit `applyTo` oder `description` im Frontmatter der Quelldatei wird die Regel direkt übernommen. Für Regeln ohne Frontmatter eine Route in `vscode-local.json` ergänzen. |
| Eigene Änderungen oder gleichnamige bestehende Skills | AgentConfig meldet den Konflikt. Die Dateien prüfen und bei Bedarf außerhalb des verwalteten Ordners sichern. |

In `~/.agentconfig/` stehen die aktuell und zuvor installierten Versionen mitsamt Quell-IDs und Dateiinhalt. `rollback` stellt den vorherigen Stand offline wieder her. Nach der Erstinstallation entfernt er die installierten Profil-Dateien. Ein abgebrochener Schreibvorgang lässt sich mit `recover` abschließen. Gleichzeitig laufende Schreibvorgänge werden gesperrt.

## WSL, Remote-SSH und Devcontainer

Wähle in **Chat: Open Customizations** die aktive Sitzung und prüfe die Herkunft der gefundenen Benutzerdateien. Installiere das Profil einmal in jedem tatsächlich verwendeten Benutzerverzeichnis. Für einen SSH-Host kannst du die drei Checkouts einmal pro Benutzer bereitstellen.

Für Devcontainer kann ein gemeinsames Template die generierten Inhalte schreibgeschützt einbinden. Dafür auf dem **Docker-Host** ein eigenes leeres Verzeichnis anlegen und mit `--home` befüllen, z. B. unter Linux mit `mkdir -p "$HOME/agentconfig-container"` und `python3 agentconfig.py apply --home "$HOME/agentconfig-container"`. Beispiel für einen WSL/Linux-Docker-Host und den Container-Benutzer `vscode`:

```jsonc
{
  "mounts": [
    "type=bind,source=${localEnv:HOME}/agentconfig-container/.copilot/instructions/ghinta,target=/home/vscode/.copilot/instructions/ghinta,readonly",
    "type=bind,source=${localEnv:HOME}/agentconfig-container/.copilot/skills,target=/home/vscode/.copilot/skills,readonly"
  ]
}
```

Das Benutzerverzeichnis und die Leserechte müssen zum Container-Benutzer passen. Der Skills-Mount belegt den ganzen Skills-Zielordner. Bei weiteren Container-Skills pro Skill separat mounten. Bei Remote-Docker müssen die Quellen auf dem Daemon-Host liegen. Nach einem Rollback auf eine leere Installation oder geänderten Mounts das Profil vor einem Rebuild erneut bereitstellen.

## Funktionstest mit Qwen

In einem unabhängigen Projekt einen frischen **Local**-Chat mit Qwen starten.

1. Über **Chat: Open Customizations** die persönlichen Instructions und die acht Skills der Startversion prüfen.
2. Eine Python-Datei an den Chat hängen und eine kleine Änderung besprechen. Unter **References** oder in der Chat-Diagnose nach `20-python-framework.instructions.md` schauen. In einem neuen Chat mit einer reinen TypeScript-Datei den Gegenversuch machen.
3. In einem frischen Chat um eine README-Einschätzung bitten. Erwartet wird kein impliziter Aufruf von `documentation`. Danach `/documentation` aus der Auswahl aufrufen und die geladenen lokalen Referenzen im Toolverlauf prüfen.

Für die Installer-Tests: `python3 -m unittest discover -s tests -v`. Sie verwenden temporäre Git-Repositories und ein isoliertes Home.

[VS Code: Instructions und Aktivierungsregeln](https://code.visualstudio.com/docs/agents/reference/custom-instructions-reference) · [Persönliche Skill-Verzeichnisse](https://code.visualstudio.com/docs/agent-customization/agent-skills) · [VS-Code-Einstellungen](https://code.visualstudio.com/docs/agents/reference/ai-settings) · [Changelog](CHANGELOG.md)
