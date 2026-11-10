# Outils de montage (scripts Resolve)

Scripts Python utilisés pour monter la Masterclass « Lili » dans DaVinci Resolve (novembre 2026).
Ils passent par `resolve_safe_mcp.py` du dossier parent. Lancer avec le venv de ce dépôt :

```
"D:\Vibe coding\davinci-resolve-mcp\.venv\Scripts\python.exe" montage_tools\sync_check.py "<timeline>"
```

Les fichiers de travail (wav, otio, json) vont dans `montage_tools/work/`, ignoré par Git.
Toujours travailler sur une copie numérotée de la timeline; ne jamais écraser celle du monteur.

## Contrôles (lecture seule) — à lancer après chaque passe

| Script | Rôle | Attendu |
|---|---|---|
| `sync_check.py <timeline>` | Partout où il y a de la voix (Audio 1/2), l'image V1/V2 vient de la même prise, à la même image. | `total 0` |
| `samecam.py plan <timeline> [x] [image de départ]` | Coupes réelles montrées deux fois sur la même caméra (jump cuts visibles). | liste vide sur la zone travaillée |
| `added_audio.py <t1> <t2> …` | Son réapparu d'une version à l'autre (plages ≥ 6 images). | rien d'imprévu |
| `alpha_profile.py <timeline>` | Transparence réelle de chaque panneau/visuel : image où il devient plein, image où il commence à disparaître. | sert à enchaîner les panneaux |
| `wave_check.py` | Coupes qui finissent sur un mot (forme d'onde). | — |
| `win_audio.py <timeline> a:b …` puis `transcribe_chk.py` | Écouter des fenêtres (images) de la timeline; transcription courte avec le venv WhisperX. | — |

## Modifications (créent une nouvelle timeline)

- `edit_pass.py <plan.json>` : retirer des plages et insérer des pauses avec panneaux (OTIO), sur toutes les pistes non graphiques.
- `restore_after_import.py <source> <nouvelle>` : après un import OTIO, remet les fondus d'entrée des panneaux (par nom ET rang, pour respecter les clips découpés à la main), les fondus audio de 3 images aux pauses et l'isolation de la voix.
- `cam_tidy.py <timeline> [décalage=True|False …]` : un bout de V2 < 6 images prend la caméra du plan qu'il prolonge; bascules explicites.

## Règles apprises (ne pas refaire ces erreurs)

1. **Image = son.** Ne jamais poser une autre image de la prise sous de la voix. La vidéo prolongée sous un fondu de panneau n'est permise que là où il n'y a pas de son. `sync_check` doit donner 0.
2. **Citations** : la citation lue reste; le panneau citation passe par-dessus pendant la lecture. Seuls les titres et numéros lus sont retirés.
3. **Restes avant un panneau** : après un titre lu retiré, retirer aussi le bout sans parole qui suit (l'intervenante regarde ses notes).
4. **Pauses d'exercice** en fin de phrase, jamais au milieu.
5. **Panneaux rapprochés** (< 1 s de caméra entre eux) : le second, sur la piste au-dessus, doit être entièrement visible quand le premier commence à disparaître (`alpha_profile`). Les Visuels, calés sur la parole, ne sont pas déplacés.
6. **Miettes de son** de 1 à 3 images : les remplacer par le plan voisin (son et image), sans changer la durée.
7. **Marqueurs** : Resolve ne les déplace pas quand on retouche; les replacer par ancrage dans les fichiers source.
8. **Repartir de la dernière timeline du monteur**, avec ses retouches (comparer sa durée à celle créée).

Portée (précisée par Alexis le 10 nov. 2026) : les fondus des panneaux (12 images d'entrée, 18 de sortie, vidéo
prolongée 12/20 images) et l'interdiction des plans < 2,5 s sont propres à la Masterclass (entrevue). En général,
la règle vaut pour tout objet posé par-dessus, selon son animation; la durée minimale dépend du type de vidéo.

## Erreurs commises pendant l'essai (à éviter par défaut)

- Image d'une autre prise posée sous la voix (V3.1, jusqu'à 3 s) : lancer `sync_check` après chaque passe.
- Citations lues retirées comme des titres (corrigé en V3.8).
- Pause d'exercice insérée au milieu d'une phrase.
- Restes de quelques images et jump cuts créés en retirant un passage au milieu d'un bout court (4 cas en V3.6).
- Jump cut sur la même caméra suivi du fondu d'un visuel; caméra non revérifiée après un déplacement de visuel.
- Plan d'appui recalé trop court (images du fichier en 25/29,97 i/s non converties) : petit trou entre deux plans.
- Copie créée pendant qu'Alexis modifiait une autre timeline : vérifier que la dernière version n'a pas changé.
- Mot « ajouté » à l'écoute qui n'existait pas (« Comment » à 19:26) : vérifier la source.
- Retranscrire après une modification : inutile, replacer les mots par ancrage source.
- Couleurs perdues à l'import OTIO, et 93 doublons sans `importSourceClips: False` + `sourceClipsFolders`.
- Ne jamais supprimer un plan (b-roll, plan tourné) sans l'accord d'Alexis : ranger les plans non utilisés après la fin.

## `exemples_lili/`

Passes propres à la vidéo Lili, avec des plages et des noms écrits en dur : à adapter, pas à relancer telles quelles.
`splice.py` (V3.8 : citations rétablies, pauses déplacées), `fix_pass.py` (V3.9 : synchro image, restes, miettes, enchaînements),
`remark.py` (marqueurs replacés depuis V3.2), `v32.py` (coupes au silence).
`finish_pass.py` (V3.1) contient le défaut de la règle 1 (image prolongée sous la voix) : ne pas le réutiliser sans le corriger.
