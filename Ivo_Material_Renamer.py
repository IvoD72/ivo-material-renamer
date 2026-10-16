# =============================================================
#  Ivo Material Renamer for 3ds Max - worker script
#  Called by Ivo_Material_Renamer.ms - do not run alone.
#
#  Reads the API key from:
#    1. <plugcfg>\IvoMaterialRenamer.ini   [API] Key=...
#    2. or the ANTHROPIC_API_KEY environment variable
# =============================================================

from pymxs import runtime as mxs
import os, io, re, json, base64, urllib.request, urllib.error

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"
THUMB = 320
TIMEOUT = 120

try:
    from PIL import Image
except Exception:
    Image = None


# ---------------- settings ----------------

def ini_path():
    try:
        return os.path.join(str(mxs.getDir(mxs.name("plugcfg"))), "IvoMaterialRenamer.ini")
    except Exception:
        return ""


def get_key():
    p = ini_path()
    if p and os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().lower().startswith("key="):
                        k = line.split("=", 1)[1].strip()
                        if k:
                            return k
        except Exception:
            pass
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _ini_value(prefix, default):
    p = ini_path()
    if p and os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().lower().startswith(prefix):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return default


def get_scope():
    """1 = SELECTED objects only (default), 2 = whole scene"""
    try:
        return int(_ini_value("scope=", "1") or 1)
    except Exception:
        return 1


def rename_textures_enabled():
    return _ini_value("renametextures=", "0") == "1"


# ---------------- scene data ----------------

def resolve_path(p):
    if not p:
        return None
    if os.path.isfile(p):
        return p
    base = mxs.maxFilePath
    if base:
        # relative to the scene folder
        cand = os.path.normpath(os.path.join(base, p))
        if os.path.isfile(cand):
            return cand
        # or in any immediate subfolder of it (Assets, textures, maps ...)
        name = os.path.basename(p)
        try:
            for sub in os.listdir(base):
                d = os.path.join(base, sub)
                if os.path.isdir(d):
                    cand = os.path.join(d, name)
                    if os.path.isfile(cand):
                        return cand
        except Exception:
            pass
    try:
        full = mxs.mapPaths.getFullFilePath(p)
        if full and os.path.isfile(full):
            return full
    except Exception:
        pass
    return None


def tex_of(mat, slot="texmap_diffuse"):
    try:
        t = mxs.getProperty(mat, slot)
    except Exception:
        return None
    if t is None:
        return None
    for attr in ("HDRIMapName", "filename"):
        try:
            p = getattr(t, attr)
            if p:
                return p
        except Exception:
            pass
    for sub in ("map", "texmap", "map1", "normal_map"):
        try:
            st = mxs.getProperty(t, sub)
            if st is not None:
                for attr in ("HDRIMapName", "filename"):
                    try:
                        p = getattr(st, attr)
                        if p:
                            return p
                    except Exception:
                        pass
        except Exception:
            pass
    return None


def collect_materials(scope):
    # scope 1 = selected objects only (default), scope 2 = whole scene
    src = list(mxs.geometry) if scope == 2 else list(mxs.selection)
    if scope != 2 and not src:
        mxs.messageBox("Nothing is selected.\n\nSelect the object(s) you want to "
                       "rename materials for, or switch to\n"
                       "'All materials in the scene'.",
                       title="Ivo Material Renamer")
        return []
    mats, seen = [], set()
    for o in src:
        m = getattr(o, "material", None)
        if m is None:
            continue
        subs = []
        if mxs.classOf(m) == mxs.MultiMaterial:
            for i in range(1, int(m.numsubs) + 1):
                sm = m[i - 1]
                if sm is not None:
                    subs.append(sm)
        else:
            subs.append(m)
        for sm in subs:
            try:
                h = mxs.getHandleByAnim(sm)
            except Exception:
                h = id(sm)
            if h not in seen:
                seen.add(h)
                mats.append(sm)
    return mats


def diffuse_rgb(m):
    try:
        c = mxs.getProperty(m, "diffuse")
        return "%d,%d,%d" % (int(c.r), int(c.g), int(c.b))
    except Exception:
        return ""


def img_b64(path):
    if Image is None or not path:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
            return ("image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii"))
    except Exception as e:
        print("  (cannot read %s: %s)" % (path, e))
        return None


def viewport_shot():
    tmp = os.path.join(os.environ.get("TEMP", "."), "_ivo_matrenamer_vp.png")
    try:
        dib = mxs.gw.getViewportDib()
        dib.filename = tmp
        mxs.save(dib)
        return img_b64(tmp)
    except Exception as e:
        print("  (viewport grab failed: %s)" % e)
        return None


# ---------------- texture file renaming (optional) ----------------

# which VRayMtl slot -> short suffix
SLOT_SUFFIX = [
    ("texmap_diffuse",             "Diff"),
    ("texmap_bump",                "Nrm"),
    ("texmap_coat_bump",           "CoatNrm"),
    ("texmap_reflection",          "Refl"),
    ("texmap_reflectionGlossiness", "Rough"),
    ("texmap_metalness",           "Metal"),
    ("texmap_refraction",          "Refr"),
    ("texmap_refractionGlossiness", "RefrRough"),
    ("texmap_opacity",             "Opacity"),
    ("texmap_displacement",        "Disp"),
    ("texmap_translucent",         "SSS"),
    ("texmap_self_illumination",   "Emissive"),
    ("texmap_sheen",               "Sheen"),
]


def _leaf_bitmaps(node, out):
    """collect VRayBitmap/Bitmaptexture nodes below a texmap"""
    if node is None:
        return
    cls = str(mxs.classOf(node))
    if cls in ("VRayBitmap", "Bitmaptexture"):
        out.append(node)
        return
    for sub in ("map", "texmap", "map1", "map2", "normal_map", "bump_map",
                "mask", "default_texmap"):
        try:
            st = mxs.getProperty(node, sub)
        except Exception:
            continue
        if st is not None:
            _leaf_bitmaps(st, out)


def _bmp_path(b):
    for attr in ("HDRIMapName", "filename"):
        try:
            p = getattr(b, attr)
            if p:
                return p
        except Exception:
            pass
    return None


def _set_bmp_path(b, newpath):
    for attr in ("HDRIMapName", "filename"):
        try:
            if getattr(b, attr):
                setattr(b, attr, newpath)
                return True
        except Exception:
            pass
    return False


def plan_texture_renames(mats, final_names):
    """
    Returns (plan, skipped)
      plan    : list of (old_abs, new_abs, [bitmap nodes])
      skipped : list of "reason" strings
    Only files that live inside the scene folder are considered.
    """
    scene_dir = str(mxs.maxFilePath or "")
    plan, skipped = [], []
    if not scene_dir:
        skipped.append("Scene is not saved yet - no scene folder to work in.")
        return plan, skipped

    scene_dir_n = os.path.normcase(os.path.normpath(scene_dir))
    by_file = {}          # abs path -> (proposed base name, [nodes])

    for m, newname in zip(mats, final_names):
        for slot, suffix in SLOT_SUFFIX:
            try:
                tex = mxs.getProperty(m, slot)
            except Exception:
                continue
            if tex is None:
                continue
            leaves = []
            _leaf_bitmaps(tex, leaves)
            for b in leaves:
                raw = _bmp_path(b)
                ap = resolve_path(raw)
                if not ap:
                    continue
                key = os.path.normcase(os.path.normpath(ap))
                if key not in by_file:
                    by_file[key] = ["%s_%s" % (newname, suffix), []]
                by_file[key][1].append(b)

    used_targets = set()
    for abspath_key, (base, nodes) in by_file.items():
        abspath = os.path.normpath(abspath_key)
        folder = os.path.normcase(os.path.dirname(abspath))
        # SAFETY: only inside the scene folder (or its subfolders)
        if not folder.startswith(scene_dir_n):
            skipped.append("outside scene folder: %s" % os.path.basename(abspath))
            continue
        ext = os.path.splitext(abspath)[1]
        target = os.path.join(os.path.dirname(abspath), base + ext)
        # avoid collisions
        n = 1
        while (os.path.normcase(target) in used_targets or
               (os.path.exists(target) and
                os.path.normcase(target) != os.path.normcase(abspath))):
            target = os.path.join(os.path.dirname(abspath),
                                  "%s_%03d%s" % (base, n, ext))
            n += 1
        if os.path.normcase(target) == os.path.normcase(abspath):
            continue  # already correct
        used_targets.add(os.path.normcase(target))
        plan.append((abspath, target, nodes))

    return plan, skipped


def apply_texture_renames(plan):
    ok, fail = 0, 0
    try:
        mxs.freeSceneBitmaps()
    except Exception:
        pass
    for old, new, nodes in plan:
        try:
            os.rename(old, new)
        except Exception as e:
            print("   FAILED %s -> %s : %s" % (os.path.basename(old),
                                               os.path.basename(new), e))
            fail += 1
            continue
        for b in nodes:
            _set_bmp_path(b, new)
        print("   %s  ->  %s" % (os.path.basename(old), os.path.basename(new)))
        ok += 1
    try:
        mxs.freeSceneBitmaps()
    except Exception:
        pass
    return ok, fail


# ---------------- API ----------------

def ask_claude(asset, items, vp):
    key = get_key()
    if not key:
        mxs.messageBox("No API key found.\n\nOpen the tool and press 'Save Key'.",
                       title="Ivo Material Renamer")
        return None

    content = [{"type": "text", "text":
        "You are helping a 3D artist name materials in a 3ds Max scene.\n"
        "For every material below, propose a SHORT, meaningful English name "
        "describing what the material IS (e.g. Oyster_Shell, Bacon, Lettuce, "
        "Porcelain, Oak_Wood, Brushed_Steel).\n\n"
        "Rules:\n"
        "- letters, digits and underscores only; start with a capital letter\n"
        "- describe the material/object, not the colour "
        "(not 'Green' but 'Lettuce')\n"
        "- do NOT add trailing numbers; duplicates are numbered automatically\n"
        "- if you truly cannot tell, repeat the current name\n\n"
        "Scene / asset: %s\n" % asset}]

    if vp:
        content.append({"type": "text", "text": "Here is the whole scene in the viewport:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": vp[0], "data": vp[1]}})

    for it in items:
        content.append({"type": "text", "text":
            "\nMaterial #%d | current name: %s | class: %s | diffuse RGB: %s | texture: %s" % (
                it["idx"], it["name"], it["cls"], it["rgb"],
                os.path.basename(it["tex"]) if it["tex"] else "(none)")})
        if it.get("img"):
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": it["img"][0], "data": it["img"][1]}})

    content.append({"type": "text", "text":
        "\n\nReturn ONLY a JSON array, no explanations, like:\n"
        '[{"idx": 1, "name": "Oyster_Shell"}, {"idx": 2, "name": "Bacon"}]'})

    payload = {"model": MODEL, "max_tokens": 1024,
               "messages": [{"role": "user", "content": content}]}

    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"x-api-key": key,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        method="POST")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        hint = ""
        if e.code == 401:
            hint = "\n\nThe key looks invalid. Save it again."
        elif e.code == 400 and "credit" in body.lower():
            hint = "\n\nYou may be out of credits:\nconsole.anthropic.com/settings/billing"
        mxs.messageBox("API error %s\n\n%s%s" % (e.code, body, hint),
                       title="Ivo Material Renamer")
        return None
    except Exception as e:
        mxs.messageBox("Could not reach the API:\n%s" % e, title="Ivo Material Renamer")
        return None

    txt = "".join(b.get("text", "") for b in data.get("content", []))
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        print("Unexpected reply:\n" + txt[:500])
        mxs.messageBox("Unexpected reply from the API - see the Listener.",
                       title="Ivo Material Renamer")
        return None
    try:
        return json.loads(m.group(0))
    except Exception as e:
        print("Cannot parse JSON: %s\n%s" % (e, txt[:500]))
        return None


# ---------------- main ----------------

def run():
    if Image is None:
        mxs.messageBox(
            "Python module 'Pillow' is missing.\n\n"
            "Open a terminal in:\n"
            "  <3ds Max install folder>\\Python\n"
            "and run:\n"
            "  .\\python.exe -m pip install --user pillow",
            title="Ivo Material Renamer")
        return

    scope = get_scope()
    mats = collect_materials(scope)
    if not mats:
        if scope == 2:
            mxs.messageBox("No materials found on scene objects.",
                           title="Ivo Material Renamer")
        return

    asset = mxs.maxFileName or "untitled scene"
    asset = re.sub(r"\.max$", "", asset, flags=re.I)

    print("\n---- Ivo Material Renamer: collecting %d materials ----" % len(mats))
    items = []
    for i, m in enumerate(mats, start=1):
        tex = resolve_path(tex_of(m))
        items.append({"idx": i, "name": str(m.name), "cls": str(mxs.classOf(m)),
                      "rgb": diffuse_rgb(m), "tex": tex or "",
                      "img": img_b64(tex) if tex else None})
        print("  [%d] %s   tex=%s" % (i, m.name,
                                      os.path.basename(tex) if tex else "-"))

    vp = viewport_shot()
    print("Asking Claude...")
    res = ask_claude(asset, items, vp)
    if not res:
        return

    proposed = {}
    for r in res:
        try:
            proposed[int(r["idx"])] = re.sub(r"[^A-Za-z0-9_]", "", str(r["name"]))[:40]
        except Exception:
            pass

    names = [(proposed.get(i) or str(m.name)) for i, m in enumerate(mats, start=1)]

    finals = []
    for i, n in enumerate(names):
        if names.count(n) > 1:
            finals.append("%s_%03d" % (n, names[:i + 1].count(n)))
        else:
            finals.append(n)

    report = "\n".join("   %s   ->   %s" % (m.name, f) for m, f in zip(mats, finals))
    print("\n---- PROPOSED NAMES ----\n" + report)

    if not mxs.queryBox("Claude proposes these names:\n\n" + report +
                        "\n\nApply them?", title="Ivo Material Renamer"):
        print(">>> Cancelled - nothing was changed. <<<")
        return

    for m, f in zip(mats, finals):
        m.name = f
    print(">>> Names applied. <<<")

    # ---- optional: rename the texture FILES too ----
    tex_msg = ""
    if rename_textures_enabled():
        if scope != 1:
            tex_msg = ("\n\nTexture files were NOT renamed:\n"
                       "that option only works in 'Only on SELECTED objects' mode.")
            print(">>> Texture renaming skipped (works on selection only). <<<")
        else:
            plan, skipped = plan_texture_renames(mats, finals)
            for s in skipped:
                print("   skipped: %s" % s)
            if not plan:
                tex_msg = "\n\nNo texture files needed renaming."
            else:
                preview = "\n".join(
                    "   %s   ->   %s" % (os.path.basename(o), os.path.basename(n))
                    for o, n, _ in plan)
                print("\n---- TEXTURE FILES TO RENAME ----\n" + preview)
                if mxs.queryBox(
                        "Rename these texture FILES on disk?\n\n" + preview +
                        "\n\n(Only files inside this scene's folder.)",
                        title="Ivo Material Renamer"):
                    ok, fail = apply_texture_renames(plan)
                    tex_msg = "\n\nTexture files renamed: %d" % ok
                    if fail:
                        tex_msg += " (%d failed - see Listener)" % fail
                    tex_msg += "\nSave the scene to keep the new paths."
                else:
                    tex_msg = "\n\nTexture files left unchanged."
                    print(">>> Texture renaming cancelled. <<<")

    mxs.messageBox("Renamed %d materials." % len(mats) + tex_msg,
                   title="Ivo Material Renamer")


run()
