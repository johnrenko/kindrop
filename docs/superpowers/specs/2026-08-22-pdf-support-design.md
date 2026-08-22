# PDF support — design

Date : 2026-08-22
Statut : validé (session autonome — décisions prises sur les options les plus simples,
cohérentes avec l'existant)

## Objectif

Accepter les fichiers `.pdf` du Source Folder comme troisième format d'entrée, à côté
des CBR/CBZ, et les convertir en EPUB Kindle par le même pipeline
(Scan → Candidate → Batch → Job → Artifact → Delivery).

## Pourquoi c'est peu invasif

KCC v11 (l'image de base `ghcr.io/ciromattia/kcc:v11.0.1`) accepte les PDF en entrée de
`c2e` : il en extrait les images (et sait rendre les PDF vectoriels). `pymupdf` est déjà
présent dans le venv de l'image Docker. La conversion et l'envoi n'exigent donc aucun
changement — seuls les points d'entrée et d'inspection du format doivent apprendre `.pdf`.

## Changements

### Backend

1. **`domain.py`** — nouvelle constante `COMIC_SUFFIXES = {".cbr", ".cbz", ".pdf"}`,
   source de vérité des formats d'entrée.

2. **`google.py` (`walk_comics`)** — remplacer le filtre littéral `{".cbr", ".cbz"}`
   par `COMIC_SUFFIXES`.

3. **`metadata.py` (`read_comic_metadata`)** — pour `.pdf` : ouvrir le document avec
   pymupdf pour valider qu'il n'est pas corrompu et contient au moins une page, puis
   retourner `ComicMetadata()` (vide). Un PDF n'embarque pas de `ComicInfo.xml`, et le
   `/Title` PDF est trop souvent pollué pour être utilisé : le titre résolu vient de
   `clean_title(nom de fichier)`, comme pour une archive sans métadonnées. Un PDF
   illisible lève `ArchiveMetadataError` → le Candidate est marqué `invalid` au scan,
   comme aujourd'hui pour une archive corrompue.

4. **`preview.py` (`extract_preview`)** — pour `.pdf` : rendre la première page en
   image via pymupdf (`get_pixmap`), puis réutiliser le chemin Pillow existant
   (downscale 480×720, JPEG). Messages d'erreur alignés sur les cas CBR/CBZ.

5. **`archives.py` (`extract_archive_images`)** — garde-fou explicite : un `.pdf` lève
   `ArchiveExtractionError("PDF files cannot be merged into a volume")` plutôt que
   l'échec opaque 7z/unrar.

6. **`api.py` (`create_batch`)** — avec `merge_by_volume`, un candidat PDF est toujours
   traité comme job individuel (jamais membre d'un groupe de fusion) : un PDF est déjà
   un volume complet, et la fusion suppose des archives d'images.

7. **`pyproject.toml`** — ajouter `pymupdf` aux dépendances backend (déjà présent dans
   l'image Docker via KCC ; nécessaire en local pour les tests).

Aucune migration : le format n'est pas une colonne, il se déduit du nom de fichier.
`kcc.py` inchangé : `c2e` reçoit le chemin source `.pdf` tel quel. La limite de
20 MiB par artifact et `--batchsplit` s'appliquent déjà.

### Frontend

- `DashboardPage.tsx` : « A scan reads new CBR and CBZ revisions » → mentionner PDF.
- Rien d'autre : la Review affiche les candidats et previews sans logique de format.

### Docs

- `docs/specs/kindrop-v1.md` : « CBR and CBZ input only » → inclure PDF.
- `CLAUDE.md` : description d'ouverture mise à jour.

## Hors périmètre (YAGNI)

- Lecture du `/Title` PDF ou d'autres métadonnées PDF.
- Fusion de chapitres PDF en volume.
- Option KCC `--pdfwidth` (rendu vectoriel basé sur la largeur) : défaut conservé.
- Tout autre format (CB7, ZIP…).

## Tests

- `test_google.py` : `walk_comics` retourne les `.pdf`.
- `test_comic_metadata.py` : PDF valide (généré via pymupdf) → métadonnées vides ;
  PDF corrompu → `ArchiveMetadataError`.
- Preview : PDF d'une page → JPEG produit ; PDF corrompu → 422 sur l'endpoint.
- `test_api.py` : `merge_by_volume` avec un PDF nommé `v01` → job individuel, pas de
  `merged_candidate_ids`.
- `test_archives.py` : `extract_archive_images` sur un PDF → erreur explicite.
- `test_workflows.py` : parcours complet d'un candidat PDF avec les fakes
  (scan → conversion → envoi).
