# nikon-transfer — contexte projet

Outil de transfert Wi-Fi Nikon D5300 → Mac via PTP/IP (CIPA DC-X 005-2005).
Stdlib uniquement pour le cœur ; PySide6 + Pillow pour la GUI.

## Lancer

```bash
# CLI
nikon-transfer --dest ~/Pictures/D5300 --new-only
nikon-transfer --debug              # hex de chaque paquet PTP/IP

# GUI (PySide6)
nikon-transfer-gui

# Tests
python -m pytest                    # 30 tests, sockets mockés

# Bundle macOS (.app double-clic)
./build_app.sh                      # → dist/Nikon Transfer.app
./build_app.sh --install            # copie aussi dans /Applications
```

Pré-requis matériel : D5300 allumé, Wi-Fi activé (Menu → Setup → Wi-Fi → Enable),
Mac connecté au SSID `Nikon_XXXXXXXX`, IP caméra par défaut `192.168.1.1:15740`.

## Carte du code

```
nikon_transfer/
  protocol.py   constantes PTP/IP, opcodes, codes réponse, rsp_name()
  client.py     PtpIpClient — handshake, _recv_data, opérations PTP
                (get_storage_ids/info, get_object_handles/info, get_object,
                 get_partial_object, get_thumb, get_device_info,
                 get_device_datetime, get_battery_level)
  transfer.py   orchestration CLI (discover_camera, transfer_photos)
  cli.py        argparse + main()
  gui.py        Qt MainWindow + CameraWorker (QThread) + PreviewDialog
  utils.py      recv_exactly, read_ptp_string, _read_uint16_array,
                format_size, format_storage_size, md5_file
  log.py        config logging (console + fichier horodaté)
tests/          test_client.py, test_transfer.py, test_utils.py
nikon_transfer_gui.py   point d'entrée PyInstaller
nikon_transfer.spec     config PyInstaller (.app, Info.plist, exclusions Qt)
build_app.sh            wrapper avec nettoyage chflags + --open / --install
assets/
  make_icon.py          régénère icon.icns depuis Pillow + iconutil
  icon.icns             icône de l'app (référencée dans le .spec)
  icon_1024.png         maître PNG (sert de source aux 10 tailles iconset)
```

## Pièges PTP/IP appris à la dure

Ces points ne se devinent pas en lisant la spec, ils ont coûté du temps de debug —
ne pas les défaire sans raison.

1. **Deux canaux obligatoires avant toute opération.** La caméra reste silencieuse sur
   `OpenSession` tant qu'on n'a pas établi le canal Event. Séquence :
   `INIT_CMD_REQUEST → INIT_CMD_ACK (donne conn_num)` sur `cmd_sock`, puis
   `INIT_EVENT_REQUEST(conn_num) → INIT_EVENT_ACK` sur un **second TCP** vers le même port.
   Voir `client.py:57-98`.

2. **Préfixe TransactionID dans les paquets DATA/DATA_END.** Chaque paquet de données
   PTP/IP commence par 4 octets de TxID *avant* la charge utile PTP réelle.
   `_recv_data` doit faire `chunks.append(chunk[4:])` (`client.py:263`).
   Symptôme si oublié : `GetStorageIDs` renvoie un ID parasite en tête, puis
   `GetObjectHandles` répond `NikonVendor:0xA081` (InvalidStorageID maquillé).

3. **Parent handle = 0** dans `GetObjectHandles`, pas `0xFFFFFFFF`.
   PTP §10.4.6 : 0 = tous objets quel que soit le parent (`client.py:153`).

4. **GUID fraîche par instance** (`uuid.uuid4().bytes`) — sinon la caméra prend la
   nouvelle connexion pour une vieille session abandonnée et refuse (`client.py:53`).

5. **Le D5300 ne pousse pas d'events PTP/IP.** Le canal event est obligatoire pour
   le handshake (piège #1), mais la caméra le ferme avec `Connection reset by peer`
   juste après l'`OpenSession` — aucun paquet 0x0008 (PKT_EVENT) ne sera jamais
   reçu. Pour détecter les nouvelles photos, polling de `GetObjectHandles` toutes
   les ~3 s avec diff vs `_known_handles` (voir `CameraWorker._poll_events` dans
   `gui.py`). La méthode `client.poll_event()` et la constante `PKT_EVENT` restent
   présentes pour un éventuel futur boîtier Nikon ou un portage à l'opcode vendor
   `NikonGetEvent 0x90C7` (voie utilisée par libgphoto2).

## GUI — décisions non évidentes

- **EXIF récupéré à la demande, pas via la miniature.** Le D5300 strippe l'EXIF des
  thumbnails embarqués. Solution : `OP_GET_PARTIAL_OBJECT (0x101B)` premiers 128 KiB
  du vrai fichier au clic, mis en cache dans `PhotoItem.exif_head`. Pillow lit l'IFD
  EXIF (sub-tag `0x8769`) depuis ce buffer. Voir `CameraWorker.fetch_exif` et
  `_format_exif` dans `gui.py`.

- **NEF non décodable nativement par Qt.** `QPixmap.loadFromData` échoue sur les RAW.
  La preview pleine résolution (`PreviewDialog`) affiche un message d'erreur explicite
  dans ce cas plutôt qu'une image vide. Pas de décodage RAW prévu (ajouter `rawpy` si
  un jour nécessaire).

- **Annulation thread-safe** via `threading.Event` lue entre deux itérations de
  download (`CameraWorker._cancel`). Une fois `GetObject` lancé sur un fichier, on
  attend qu'il se termine — pas d'interruption en plein transfert.

- **Tri dynamique** sans tout réindexer : `PhotoItem.__lt__` lit `_sort_key`,
  `_resort` recalcule les clés puis appelle `sortItems()`.

- **Reconnexion auto sur socket cassée.** Le `cmd_sock` du D5300 tombe sans prévenir
  (idle, ou pendant un download). `CameraWorker._mark_disconnected` ferme la session
  et émet `connection_lost(str)` au lieu de `error+disconnected` — `MainWindow.
  _on_connection_lost` enchaîne 3 tentatives (backoff 2/4/8 s via `QTimer.singleShot`)
  avant de remonter une `QMessageBox`. Le tuple `_DEAD_SOCKET_ERRORS` (BrokenPipe,
  ConnectionReset/Aborted, EOF) doit être attrapé partout où l'on parle au PTP
  (fetch_full, fetch_exif, _poll_events, download, _refresh_battery). Pendant
  une reconnexion, `_fresh_session_reset` vide la grille — `_on_photo_found` dédoublonne
  par handle pour que la ré-énumération ne crée pas de doublons. Le flag
  `_user_initiated_disconnect` (mis dans `closeEvent`) empêche les retries après
  fermeture de fenêtre.

- **GetDeviceInfo / DateTime / BatteryLevel sont nice-to-have.** Une caméra firmware
  exotique peut retourner `DevicePropNotSupported (0x200A)` — `get_device_datetime`
  et `get_battery_level` renvoient `None` dans ce cas plutôt que de lever. Le
  handshake `connect_camera` log-et-ignore l'échec de `get_device_info` /
  `get_device_datetime` pour ne pas casser la connexion.

## Limites connues

- **Débit ~1–2 Mo/s** plafonné par le Wi-Fi 802.11g du D5300 — pas un problème Python.
- **Pas d'aperçu RAW** (NEF) en preview pleine résolution.
- **Reprise réseau seulement *entre* fichiers.** L'auto-reconnect (cf. GUI) relance
  la session après une coupure, mais un téléchargement interrompu en plein milieu
  n'est pas repris : le fichier en cours est marqué erreur et la boucle s'arrête.
  La sélection cochée reste, donc relancer « Télécharger » suffit en pratique.

## Conventions

- Strings et logs en français (utilisateur francophone).
- Stdlib only pour `nikon_transfer.client/transfer/cli` — pas de dépendances cachées.
  PySide6 + Pillow uniquement pour la GUI (extra `[gui]` dans `pyproject.toml`).
  PyInstaller pour le bundle macOS (extra `[build]`).
- Tests Python 3.11+ (`requires-python = ">=3.11"`), sockets mockés intégralement —
  jamais besoin d'une caméra réelle pour `pytest`.
- CI : `.github/workflows/tests.yml` lance `pytest --cov` sur Ubuntu × {3.11, 3.12,
  3.13} + un run macOS 3.12. Aucun secret requis, aucune dépendance externe — les
  tests sont 100 % stdlib + `pytest`/`pytest-cov` (extra `[dev]`).
- SAST : `.github/workflows/sast.yml` lance `bandit` (config dans `[tool.bandit]`
  de `pyproject.toml`) sur le package, fail si severity ≥ MEDIUM. Upload SARIF
  vers l'onglet Security du repo. **B110 / B112 skipés** : nos `try/except: pass`
  sur les sockets et le parse EXIF sont du *best-effort* défensif intentionnel —
  ne pas les transformer en `log+raise` sans réfléchir.
- Tailles d'octets : `format_size` (base 1024, pour la taille des fichiers)
  vs `format_storage_size` (base 1000 SI, pour l'affichage carte mémoire — colle
  à l'étiquette « 32 Go » du fabricant). Ne pas mélanger.
- Version : **source unique** dans `nikon_transfer/__init__.py:__version__`.
  Lue par `pyproject.toml` (`dynamic = ["version"]` + `[tool.setuptools.dynamic]`),
  par la CLI (`--version`), par la GUI (titre de fenêtre), et par le `.spec`
  PyInstaller (via parsing AST pour éviter d'importer le package — sinon ça
  trigger PySide6 au build). Ne jamais hardcoder la version ailleurs.
