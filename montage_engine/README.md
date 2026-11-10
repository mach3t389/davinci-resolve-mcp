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
| `cli.py` | `check` et `words` en ligne de commande | non |

## Utilisation (Resolve ouvert, scripting externe activé)

```
.venv\Scripts\python.exe -m montage_engine.cli check "Lili V4.1 - ..." --type interview --series masterclass --waveform --explain
.venv\Scripts\python.exe -m montage_engine.cli words "Lili V4.1 - ..." "F:\Masterclass\...\Transcriptions"
"D:\Vibe coding\whisperx\.venv\Scripts\python.exe" -m montage_engine.transcribe_source "<dossier Transcriptions>" "<rush1.mov>" "<rush2.mov>"
```

## Portée des règles

Les règles de la Masterclass (part 75/25 des caméras, fondus 12/18 des panneaux, Visuels) vivent dans `SERIES['masterclass']`,
pas dans les valeurs générales. La durée minimale d'un plan (2,5 s) vient du type de vidéo `interview`.
Ces trois niveaux sont les mêmes que ceux des directives de montage de Rushflow (doc 25, section 4).

## Limites connues

- Jamais essayé sur une vraie timeline : Resolve n'était pas ouvert pendant l'écriture. Premier essai à faire en lecture seule
  (`cli check` sur Lili V4.1), puis `run_pass` sur une copie.
- `passes.extend_to_silence` calcule la prolongation d'une coupe jusqu'au silence; l'appliquer (avec décalage) reste à écrire.
  Pour l'instant une coupe sans silence proche reçoit un marqueur jaune « Coupe a ecouter ».
- Le contrôle « les correctifs du client sont-ils tous traités ? » dépend de la boucle de révision (étape 5, côté Rushflow).
