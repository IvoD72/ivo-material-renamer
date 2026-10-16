# Ivo Material Renamer for 3ds Max

Gives your materials real names. Instead of `Material #36`, `02` or `VRayMtl_07`
you get `Oak_Wood`, `Brushed_Steel`, `Oyster_Shell`.

It sends each material's texture and a viewport grab to Claude, gets a name back,
shows you the full list, and renames only after you confirm. Duplicates are
numbered `_001`, `_002` automatically.

> **Download:** grab the two script files from
> [Releases](../../releases) (or clone the repo).

---

## What it does

* Reads the materials of the selected objects (or of the whole scene)
* Looks at the diffuse texture of each one and at the viewport
* Proposes a descriptive name for every material
* Shows the proposals — nothing is renamed until you confirm
* Numbers duplicates `_001`, `_002`, …
* Optionally renames the texture **files** on disk to match, and fixes the paths
  in the scene

Works on Multi/Sub-Object materials as well.

## Requirements

* 3ds Max 2021 or newer (needs Python 3)
* Pillow — if the tool says it is missing, open a terminal in
  `<3ds Max install folder>\Python` and run
  `.\python.exe -m pip install --user pillow`
* Internet access
* An Anthropic (Claude) API key — see below

## Install

1. Keep **both** files in the same folder:
   * `Ivo_Material_Renamer.ms`
   * `Ivo_Material_Renamer.py`
2. In 3ds Max: **Scripting → Run Script…** and pick the `.ms` file. The tool opens.
3. Optional toolbar button: **Customize → Customize User Interface → Toolbars**,
   Category **Ivo Tools**, drag **Mat Renamer** onto a toolbar.

If you move the folder later, run the `.ms` once from the new location so the
button points at the right file.

## API key

1. Go to <https://console.anthropic.com> and create an account
2. **Settings → API keys → Create Key**, copy it immediately (shown only once)
3. **Settings → Billing → Buy credits** — $5–10 covers hundreds of assets
4. Paste the key in the tool and press **Save Key**

The key is stored locally in `<plugcfg>\IvoMaterialRenamer.ini`. It never goes
anywhere except to Anthropic's API. You can also set an `ANTHROPIC_API_KEY`
environment variable instead and leave the field empty.

**Cost:** roughly 1–3 US cents per asset. You pay Anthropic directly through
your own account — this tool takes no cut and has no server of its own.

## Renaming texture files (optional, off by default)

The checkbox **Also rename texture FILES on disk** renames the image files to
match the new material names:

```
Map #2037_16.png   ->   Oyster_Shell_Diff.png
OSLMap_27.png      ->   Oyster_Shell_Rough.png
```

Safety rules built in:

* available only in **Only on SELECTED objects** mode
* touches only files inside the scene's own folder or its subfolders — shared
  textures stored elsewhere are never renamed
* never overwrites an existing file (adds `_001` if needed)
* shows the full list and asks again before renaming
* updates all bitmap paths in the scene

If another scene points at the same local files, it will lose them. Save your
scene after renaming so the new paths are stored.

## Tips

* It works on the **selection** by default, so it will not touch the rest of your
  scene. Switch to "All materials in the scene" only if you mean it.
* Run it **after** textures are assigned — that is what the AI looks at.
* Undo does not cover material renaming. Save before a big run.

## Support

Found a bug or want a feature? Open an issue.

If the tool is useful, you can support it on Gumroad — pay what you want,
zero is fine. _(link coming)_

## License

MIT — see [LICENSE](LICENSE). Do what you like with it, just keep the notice.

Not affiliated with Autodesk or Anthropic.
