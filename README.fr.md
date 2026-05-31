# nikon-transfer

> 🇬🇧 [Read in English](README.md)

Transfert Wi-Fi de photos depuis un **Nikon D5300** vers un Mac (ou tout autre
ordinateur) via PTP/IP. Pas de câble USB, pas d'app propriétaire (Nikon WMU est
abandonnée et cassée sur les macOS récents), pas de `libgphoto2`. Le cœur est
en **Python stdlib pur** ; la GUI optionnelle ajoute PySide6 + Pillow.

[![tests](https://github.com/doby/nikon-transfer-wifi-dseries/actions/workflows/tests.yml/badge.svg)](https://github.com/doby/nikon-transfer-wifi-dseries/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![license](https://img.shields.io/badge/license-MIT-lightgrey)]()

## ⚠️ Avertissement

Ce logiciel est fourni « tel quel », sans aucune garantie. Il communique avec
la caméra via un protocole PTP/IP reverse-engineered et non couvert par la
garantie Nikon. L'auteur décline toute responsabilité en cas de
dysfonctionnement, perte de données, ou dommage matériel résultant de son
utilisation. Voir [LICENSE](LICENSE).

## Capture d'écran

> _Capture GUI à ajouter — voir `docs/screenshot.png`._

## Fonctionnalités

- **CLI** pour les transferts scriptés (`--new-only`, `--dry-run`, filtre
  d'extensions…).
- **GUI** avec grille de miniatures, tri par nom / date de prise de vue / taille,
  panneau EXIF à la demande, aperçu pleine résolution au double-clic.
- **Synchro automatique** : les photos prises pendant que la GUI est connectée
  apparaissent dans la grille en ~3 s, sans rechargement manuel.
- **Aucune dépendance runtime externe** pour la CLI/cœur (stdlib uniquement).

## Pré-requis

| Étape  | Action                                                                 |
|--------|------------------------------------------------------------------------|
| Caméra | Menu → Setup ⚙ → Wi-Fi → **Enable**                                    |
| Mac    | Se connecter au réseau Wi-Fi de la caméra (`Nikon_XXXXXXXX`)           |
| Python | 3.11 ou plus récent                                                    |

IP par défaut de la caméra : `192.168.1.1`, port PTP/IP `15740`.

Testé sur **MacBook Apple Silicon (M1)** sous macOS. Devrait fonctionner sur
Intel Mac, Linux et Windows (cœur Python pur), mais non vérifié.

## Installation

```bash
pip install .             # CLI seule
pip install ".[gui]"      # CLI + GUI
pip install ".[progress]" # ajoute une barre tqdm à la CLI
```

## Usage

### CLI

```
nikon-transfer [options]

Options :
  --host HOST       IP de la caméra (défaut : 192.168.1.1)
  --dest PATH       Dossier de destination (défaut : ~/Pictures/Nikon_D5300)
  --new-only        Ignore les fichiers déjà présents dans la destination
  --dry-run         Liste les fichiers sans télécharger
  --ext EXT [EXT …] Extensions à transférer (défaut : .jpg .nef .tif .png …)
  --debug           Log chaque paquet PTP/IP en hex (verbeux)
```

Exemples :

```bash
nikon-transfer                                            # tout transférer
nikon-transfer --dest ~/Desktop/Shoot --new-only --ext .nef
nikon-transfer --dry-run                                  # lister, sans télécharger
nikon-transfer --host 192.168.0.10                        # IP caméra différente
```

### GUI

```bash
nikon-transfer-gui
```

Clic sur **Connecter**, attendre le chargement des miniatures, cocher les photos
voulues, puis **Télécharger la sélection**. Prends une nouvelle photo pendant
que la GUI est connectée — elle apparaît automatiquement dans la grille.

### Application macOS double-clic

Pour générer un `Nikon Transfer.app` autonome (sans Python à installer chez
l'utilisateur final) :

```bash
pip install ".[build]"     # installe PyInstaller
./build_app.sh             # → dist/Nikon Transfer.app
./build_app.sh --install   # copie le .app dans /Applications
```

Le bundle pèse ~100 Mo (Qt + Python embarqués). Au premier lancement, macOS
demande l'autorisation réseau local pour que l'app puisse parler à la caméra.

## Comment ça marche

L'outil parle **PTP/IP** (CIPA DC-X 005-2005) directement sur le port TCP `15740`.
Deux connexions TCP sont établies (commande + event) et un petit sous-ensemble
des opérations PTP est utilisé : `OpenSession`, `GetStorageIDs`,
`GetObjectHandles`, `GetObjectInfo`, `GetObject`, `GetThumb`, `GetPartialObject`.

## Pièges PTP/IP (appris à la dure sur le D5300)

Ces points ne se devinent pas dans la spec — voir `CLAUDE.md` pour le détail.

1. **Deux canaux TCP obligatoires avant toute opération PTP.** La caméra reste
   silencieuse sur `OpenSession` tant que le handshake event n'est pas fait.
2. **Les paquets DATA / DATA_END sont préfixés par 4 octets de TransactionID**
   avant la charge utile PTP réelle. Sans les retirer, `GetStorageIDs` renvoie
   un ID parasite en tête.
3. **`parent=0`** dans `GetObjectHandles` signifie « tous les objets quel que soit
   le parent » (pas `0xFFFFFFFF`).
4. **GUID fraîche par instance** (`uuid.uuid4().bytes`) — la réutiliser fait
   que la caméra prend la connexion pour une session abandonnée et la refuse.
5. **Le D5300 ne pousse pas d'events PTP/IP.** Le canal event est obligatoire
   pour le handshake mais la caméra le ferme juste après. La détection de
   nouvelles photos passe donc par un polling de `GetObjectHandles` toutes les
   ~3 s plutôt que par le canal event.

## Développement

```bash
pip install -e ".[gui,dev]"
pytest             # 25 tests, sockets entièrement mockés — pas besoin de caméra
pytest --cov
```

## Limitations

- Débit **~1–2 Mo/s**, plafonné par le Wi-Fi 802.11g du D5300 (pas Python).
- **Pas de preview RAW (NEF) native** dans l'aperçu pleine résolution (Qt ne
  décode pas le NEF — il faudrait `rawpy` ou équivalent).
- **Pas de reprise après erreur réseau en cours de transfert** — se reconnecter
  manuellement.
- Testé uniquement sur **D5300**. PRs bienvenues pour d'autres boîtiers Nikon —
  voir [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Licence

[MIT](LICENSE)
