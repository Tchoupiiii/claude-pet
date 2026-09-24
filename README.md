# Claude Pet

Un petit compagnon en pixel art qui vit sur ton bureau et montre ce que fait Claude Code en ce moment.

## États

Le pet change d'animation selon l'état écrit dans `state.txt` :

| État | Signification |
| --- | --- |
| `working` | Claude travaille |
| `idle` | Claude attend |
| `question` | Claude te pose une question |
| `sleeping` | Aucune session active |
| `tired HH:MM` | Il se fait tard |

Tu peux remplir `state.txt` avec des hooks Claude Code (par exemple `UserPromptSubmit` → `working`, `Stop` → `idle`).

## Utilisation

Il faut Python 3 avec Tkinter (inclus par défaut).

```bash
python pet.pyw
```

- **Windows** : il s'affiche en bas à droite, avec une icône dans la barre des tâches.
- **macOS** : il s'affiche en haut au centre, sous l'encoche.
- **Glisser** le pet pour le déplacer. Sa position est mémorisée.
- **Clic droit** : déplacer, changer la taille, masquer ou quitter.

Sur Windows, `kill_pet.bat` arrête le pet.
