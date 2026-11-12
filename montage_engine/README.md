# montage_engine

Moteur de montage extrait de l'essai « Masterclass Lili » (9 nov. 2026). Voir le document Rushflow 25.
Remplace peu à peu les scripts de `montage_tools/` (gardés comme référence : valeurs écrites en dur).

| Module | Rôle | Testé sans Resolve |
|---|---|---|
| `model.py` | Instantané simple d'une timeline (`Snapshot`, `Clip`); `snapshot_from_resolve` | oui (via des faux) |
| `series.py` | Directives par niveaux (général → type → série → vidéo) avec provenance; rôles des pistes par NOM | oui |
| `anchors.py` | Transcription ancrée sur les fichiers source (`<projet>/Transcriptions/<fichier>.words.json`), mots et marqueurs replacés sur n'importe quelle timeline | oui |
| `transcribe_source.py` | Transcrit une source une seule fois (Whisper + alignement), à lancer avec le venv WhisperX | non (modèles) |
| `checks.py` | Contrôles en lecture seule : image = son, plans courts, part des caméras, jump cuts, coupes dans un fondu, trous de plans d'appui, restes, coupes sur du son (forme d'onde) | oui |
| `passes.py` | Passe OTIO générique (retirer des plages, insérer des pauses), versions numérotées, coupes au silence, restauration des fondus après import | OTIO oui; adaptateur Resolve non |
| `panels.py` | Fondus d'un rendu **lus** dans son fichier d'infos `<rendu>.mov.motion.json` (le moteur Motion les connaît déjà); mesure ffmpeg de la transparence seulement en repli; sorte de panneau (chapitre ou par-dessus la parole); couverture (plein écran ou partielle); enchaînement des panneaux rapprochés | oui |
| `cameras.py` | Plan d'alternance des caméras selon les directives (part, durées, jump cuts, zones de fondu) et son application sur une copie | oui (plan); l'application non |
| `broll.py` | Mini-scènes de b-roll, plans non utilisés rangés après la fin (jamais supprimés) | oui |
| `audio_rules.py` | Règles du son des directives : pauses raccourcies (`cuts.keep_pause_seconds`), son en avance / coupe en J (`cuts.audio_lead_frames`), niveau sonore (`audio.target_lufs`, via `NormalizeAudioLevel` de Resolve). La baisse de la musique sous la voix n'est pas applicable (pas d'automation du volume dans l'API) | oui (pauses, coupe en J); niveau sonore avec un faux Resolve |
| `cli.py` | `check` et `words` en ligne de commande | non |

## Utilisation (Resolve ouvert, scripting externe activé)

```
.venv\Scripts\python.exe -m montage_engine.cli check "Lili V4.1 - ..." --type interview --series masterclass --waveform --explain
.venv\Scripts\python.exe -m montage_engine.cli words "Lili V4.1 - ..." "F:\Masterclass\...\Transcriptions"
"D:\Vibe coding\whisperx\.venv\Scripts\python.exe" -m montage_engine.transcribe_source "<dossier Transcriptions>" "<rush1.mov>" "<rush2.mov>"
```

## Fondus des panneaux : lus, pas calculés (précision d'Alexis, 10 nov. 2026)

Le moteur d'animation (Motion/Remotion) connaît les fondus de ce qu'il rend. L'étape de rendu doit donc écrire à côté du
`.mov` un fichier `<nom>_v001.mov.motion.json` :

```json
{"n": 100, "in_full": 0, "out_start": 82, "coverage": "fullscreen"}
```

`n` = images du rendu, `in_full` = première image pleinement visible, `out_start` = image où le fondu de sortie commence,
`coverage` = `fullscreen` (transition intégrée à un panneau plein écran) ou `partial` (overlay sur l'image, possiblement
sur plusieurs plans). Seul un panneau plein écran contraint les coupes autour de ses fondus; un overlay `partial` laisse
la caméra changer dessous. Sans fichier d'infos (stock, visuels tiers), `--measure-alpha` mesure la transparence avec ffmpeg
et en déduit la couverture; sans mesure, le contrôle reste prudent (plein écran, fondus des directives).
`panels.write_sidecar(...)` écrit ce fichier. À brancher dans l'étape de rendu de Motion (`npm run render`) et du module Motion de Desktop.

## Portée des règles

Les règles de la Masterclass (part 75/25 des caméras, fondus 12/18 des panneaux, Visuels) vivent dans `SERIES['masterclass']`,
pas dans les valeurs générales. La durée minimale d'un plan (2,5 s) vient du type de vidéo `interview`.
Ces trois niveaux sont les mêmes que ceux des directives de montage de Rushflow (doc 25, section 4).

## Limites connues

- Règles du son (12 nov. 2026) : codées et testées sans Resolve. Elles s'appliquent à la copie créée par `pass --apply` (`--pauses <dossier Transcriptions>` pour les pauses). Jamais lancées sur une vraie timeline; le moteur lit encore les règles de `series.py`, pas celles enregistrées dans Rushflow.

- Les contrôles ont été validés en lecture seule sur Lili (V3.1 : défaut connu retrouvé; V3.9 et V4.1 : 0 écart image/son).
  Aucune passe qui modifie une timeline n'a encore été lancée sur une vraie copie (`passes.run_pass`, `restore_after_import`,
  `cameras.apply_runs`) : DaVinci n'est pas utilisé pour les essais de mutation tant qu'Alexis n'a pas donné son accord.
- `passes.plan_silence_extensions` + `transform_otio(extend=True)` déplacent les coupes vers le silence (testé sur OTIO simulé);
  une coupe sans silence à 8 images reçoit un marqueur jaune « Coupe a ecouter » (à poser avec `pass_markers`).
- `cameras.plan_runs` propose; la part visée vient de la série (`cameras.main_share`). La cible exacte dépend des couvertures
  par les plans d'appui et les panneaux : `checks.check_camera_share` mesure le résultat réel.
- Le contrôle « correctifs du client tous traités » dépend de la boucle de révision (étape 5, côté Rushflow).
- Outil MCP `check_timeline` (lecture seule) dans `resolve_safe_mcp.py`; le redémarrage du serveur MCP est nécessaire pour qu'il apparaisse.
