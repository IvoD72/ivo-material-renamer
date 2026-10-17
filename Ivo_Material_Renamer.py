# =============================================================
#  Ivo Material Renamer for 3ds Max v1.3 - worker script
#  Called by Ivo_Material_Renamer.ms - do not run alone.
#
#  Reads the API key from:
#    1. <plugcfg>\IvoMaterialRenamer.ini   [API] Key=...
#    2. or the ANTHROPIC_API_KEY environment variable
# =============================================================

import pymxs
from pymxs import runtime as mxs
import os, io, re, json, base64, filecmp, shutil, urllib.request, urllib.error

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"
THUMB = 320
TIMEOUT = 120
#: Materials per request. Keeps every request well under the API's limits on
#: images and reply length, whatever the size of the scene.
BATCH = 30   # up to 2 pictures per material (its part + its texture)
VERSION = "1.3"
TITLE = "Ivo Material Renamer " + VERSION

try:
    from PIL import Image
except Exception:
    Image = None


def ask(text):
    return bool(mxs.queryBox(text, title=TITLE))


def info(text):
    mxs.messageBox(text, title=TITLE)


# ---------------- settings ----------------

def ini_path():
    try:
        return os.path.join(str(mxs.getDir(mxs.name("plugcfg"))), "IvoMaterialRenamer.ini")
    except Exception:
        return ""


def _ini_value(name, default=""):
    """A value from [API] in the tool's INI.

    Read through 3ds Max itself: setINISetting writes the file as UTF-16, and
    reading it as UTF-8 (v1.0-v1.1) found nothing - the texture options were
    always off and the key came only from ANTHROPIC_API_KEY."""
    p = ini_path()
    if p and os.path.isfile(p):
        try:
            v = str(mxs.getINISetting(p, "API", name) or "").strip()
            if v:
                return v
        except Exception as e:
            print("  (cannot read %s from %s: %s)" % (name, p, e))
    return default


def get_key():
    return _ini_value("Key") or os.environ.get("ANTHROPIC_API_KEY", "").strip()


def get_scope():
    """1 = SELECTED objects only (default), 2 = whole scene"""
    try:
        return int(_ini_value("Scope", "1") or 1)
    except Exception:
        return 1


def rename_textures_enabled():
    return _ini_value("RenameTextures", "0") == "1"


def collect_textures_enabled():
    return _ini_value("CollectTextures", "0") == "1"


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
        # or by its file name next to the scene or in a subfolder up to three
        # levels down (Maps, Texture, tex\4k ...). Downloaded models often keep
        # the author's own path (D:\3ds max\...) or a bare file name.
        name = os.path.basename(p.replace("/", "\\")).lower()
        hit = _files_under(os.path.normpath(str(base))).get(name)
        if hit and os.path.isfile(hit):
            return hit
    try:
        full = mxs.mapPaths.getFullFilePath(p)
        if full and os.path.isfile(full):
            return full
    except Exception:
        pass
    return None


_FILES = {}


def _files_under(root, levels=3):
    """{lower-case file name: path} next to the scene and up to `levels` deep.
    Built once per folder; the first file found by a name wins."""
    if root not in _FILES:
        idx, depth0 = {}, root.count(os.sep)
        try:
            for d, subs, files in os.walk(root):
                if d.count(os.sep) - depth0 >= levels:
                    subs[:] = []
                for fn in files:
                    idx.setdefault(fn.lower(), os.path.join(d, fn))
        except Exception as e:
            print("  (cannot search %s: %s)" % (root, e))
        _FILES[root] = idx
    return _FILES[root]


def _inside(path, folder):
    """True if path is inside folder or one of its subfolders."""
    if not folder:
        return False
    f = os.path.normcase(os.path.normpath(folder))
    return os.path.normcase(os.path.normpath(path)).startswith(f + os.sep)


# Texture slots are found by name, so the same code covers VRayMtl, Physical
# Material, Standard, CoronaPhysicalMtl and others. First match wins: the more
# specific words come first ("coat_bump" before "bump", "refr" before "refl").
SUFFIX_RULES = [
    (("coat_bump", "coatbump", "clearcoatbump"), "CoatNrm"),
    (("bump", "normal"), "Nrm"),
    (("refractionglossiness", "refraction_gloss", "refraction_rough", "refrrough"), "RefrRough"),
    (("refr", "transparency", "trans_"), "Refr"),
    (("rough", "gloss"), "Rough"),
    (("metal",), "Metal"),
    (("refl", "specular"), "Refl"),
    (("opacity", "cutout"), "Opacity"),
    (("displac",), "Disp"),
    (("translucen", "sss", "scatter"), "SSS"),
    (("self_illum", "selfillum", "emi"), "Emissive"),
    (("sheen",), "Sheen"),
    (("coat",), "Coat"),
    (("diffuse", "base_color", "basecolor", "basetexmap", "base_texmap", "color"), "Diff"),
]


def slot_suffix(prop_name):
    n = prop_name.lower()
    for words, suffix in SUFFIX_RULES:
        if any(w in n for w in words):
            return suffix
    return "Map"


def _is_texmap(v):
    try:
        return mxs.superClassOf(v) == mxs.textureMap
    except Exception:
        return False


#: Standard material keeps its maps behind these names - getPropNames does
#: not list them.
EXTRA_SLOTS = ("diffuseMap", "ambientMap", "specularMap", "specularLevelMap", "glossinessMap",
               "selfIllumMap", "opacityMap", "filterMap", "bumpMap", "reflectionMap",
               "refractionMap", "displacementMap")


def _texmap_props(node):
    """[(property name, texmap)] for every texture map plugged into node."""
    out = []
    try:
        names = [str(n) for n in mxs.getPropNames(node)]
    except Exception:
        names = []
    low = {n.lower().lstrip("#") for n in names}
    names += [n for n in EXTRA_SLOTS if n.lower() not in low]
    named, listed = [], []
    for n in names:
        try:
            v = mxs.getProperty(node, n)
        except Exception:
            continue
        if v is None:
            continue
        if _is_texmap(v):
            named.append((str(n), v))
        elif _is_array(v):
            # Composite (mapList), CoronaMultiMap (texmaps) ... keep maps in a list
            for k, e in enumerate(v, start=1):
                if e is not None and _is_texmap(e):
                    listed.append(("%s_%d" % (n, k), e))
    # A map in a named slot keeps that slot's name: Standard also lists all its
    # maps in `maps`, which would otherwise turn diffuseMap into "maps_2".
    have = {_handle(v) for _, v in named}
    return named + [(n, v) for n, v in listed if _handle(v) not in have]


def _is_array(v):
    # map lists in parameter blocks come back as ArrayParameter, not Array
    try:
        return str(mxs.classOf(v)) in ("Array", "ArrayParameter")
    except Exception:
        return False


def _bmp_path(b):
    # Bitmaptexture / CoronaBitmap / OSLMap: filename; VRayBitmap: HDRIMapName
    for attr in ("HDRIMapName", "filename"):
        try:
            p = mxs.getProperty(b, attr)
            if p:
                return str(p)
        except Exception:
            pass
    return None


def _set_bmp_path(b, newpath):
    for attr in ("HDRIMapName", "filename"):
        try:
            if mxs.getProperty(b, attr):
                mxs.setProperty(b, attr, newpath)
                return True
        except Exception:
            pass
    return False


def _leaf_bitmaps(node, out, seen=None):
    """Every map below `node` that points at an image file."""
    if node is None:
        return
    if seen is None:
        seen = set()
    try:
        h = mxs.getHandleByAnim(node)
    except Exception:
        h = id(node)
    if h in seen:
        return
    seen.add(h)
    if _bmp_path(node):
        out.append(node)
        return
    for _, st in _texmap_props(node):
        _leaf_bitmaps(st, out, seen)


def tex_of(mat):
    """The diffuse / base color image of a material, for the thumbnail."""
    for name, tex in _texmap_props(mat):
        if slot_suffix(name) == "Diff":
            leaves = []
            _leaf_bitmaps(tex, leaves)
            for b in leaves:
                p = _bmp_path(b)
                if p:
                    return p
    return None


def sub_mtls(m):
    """The materials inside a container: Multi/Sub-Object, Blend, Double Sided,
    VRayBlendMtl, VRay2SidedMtl, CoronaLayeredMtl ... Empty for a plain one."""
    try:
        n = int(mxs.getNumSubMtls(m))
    except Exception:
        return []
    out = []
    for i in range(1, n + 1):
        try:
            sm = mxs.getSubMtl(m, i)
        except Exception:
            continue
        if sm is not None:
            out.append(sm)
    return out


def is_multi(m):
    return bool(sub_mtls(m))


def _handle(m):
    try:
        return mxs.getHandleByAnim(m)
    except Exception:
        return id(m)


def multi_parts(mats):
    """{index of a Multi/Sub-Object in mats: [1-based numbers of its parts]}"""
    pos = {_handle(m): i for i, m in enumerate(mats, start=1)}
    out = {}
    for i, m in enumerate(mats, start=1):
        subs = sub_mtls(m)
        if subs:
            out[i] = [pos[_handle(sm)] for sm in subs if _handle(sm) in pos]
    return out


def make_batches(items):
    """Up to BATCH items per request; a Multi/Sub-Object stays in one request
    with its parts (they follow it in the list), so Claude sees them together."""
    groups, i = [], 0
    while i < len(items):
        g = [items[i]]
        parts = set(items[i].get("parts") or [])
        i += 1
        while parts and i < len(items) and items[i]["idx"] in parts:
            g.append(items[i])
            i += 1
        groups.append(g)
    batches, cur = [], []
    for g in groups:
        if cur and len(cur) + len(g) > BATCH:
            batches.append(cur)
            cur = []
        cur = cur + g
    if cur:
        batches.append(cur)
    return batches


def collect_materials(scope):
    # scope 1 = selected objects only (default), scope 2 = whole scene
    src = list(mxs.geometry) if scope == 2 else list(mxs.selection)
    if scope != 2 and not src:
        info("Nothing is selected.\n\nSelect the object(s) you want to "
             "rename materials for, or switch to\n"
             "'All materials in the scene'.")
        return []
    mats, seen = [], set()

    def add(m):
        # a container gets a name too, right before its parts; nested ones too
        h = _handle(m)
        if h in seen:
            return
        seen.add(h)
        mats.append(m)
        for sm in sub_mtls(m):
            add(sm)

    for o in src:
        m = getattr(o, "material", None)
        if m is not None:
            add(m)
    return mats


def diffuse_rgb(m):
    for prop in ("diffuse", "base_color", "basecolor", "diffuseColor"):
        try:
            c = mxs.getProperty(m, prop)
            return "%d,%d,%d" % (int(c.r), int(c.g), int(c.b))
        except Exception:
            pass
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


# ---------------- a picture of every part ----------------
#
# One shot of the whole model is not enough: Claude cannot tell which slot is
# the pump and which the label, and guesses by order (seen on a lotion bottle:
# the pump, 56% of the faces at the top, came back as "Bottle_Plastic"). So
# each material also gets a shot of ONLY its own part. The objects are never
# changed: a temporary copy of the mesh with just that material's faces is
# shown alone, grabbed and deleted, and the scene is put back as it was.

_MXS_HELPERS = r"""
global ivoMR_makePart
global ivoMR_tris
fn ivoMR_tris o =
(
    local c = 0
    try ( local m = snapshotAsMesh o; c = m.numfaces ) catch ()
    c
)
fn ivoMR_makePart src matId mtl =
(
    local msh = snapshotAsMesh src
    if matId > 0 do
    (
        local del = #{}
        for f = 1 to msh.numfaces where (getFaceMatID msh f) != matId do append del f
        meshop.deleteFaces msh del delIsoVerts:true
    )
    if msh.numfaces == 0 then undefined else
    (
        local n = mesh mesh:msh
        n.name = uniqueName "_ivoMR_part_"
        n.material = mtl
        n
    )
)
"""


def _parts_of(mats, objs):
    """{material handle: [(object, Material ID, or 0 for the whole object)]}"""
    want = {_handle(m) for m in mats}
    parts = {}

    def walk(m, o, mid):
        if m is None:
            return
        if mxs.classOf(m) == mxs.MultiMaterial:
            if _handle(m) in want:
                parts.setdefault(_handle(m), []).append((o, mid))
            for sm, sid in zip(m.materialList, m.materialIDList):
                walk(sm, o, mid or int(sid))
            return
        if _handle(m) in want:
            parts.setdefault(_handle(m), []).append((o, mid))
        for sm in sub_mtls(m):          # Blend, Double Sided, Layered ...
            walk(sm, o, mid)

    for o in objs:
        walk(getattr(o, "material", None), o, 0)
    return parts


def _grab(path):
    mxs.completeRedraw()
    dib = mxs.gw.getViewportDib()
    dib.filename = path
    mxs.save(dib)
    return img_b64(path)


def part_shots(mats, objs):
    """{material handle: {"img", "faces", "pct", "zone", "box"}}

    The scene stays 1:1: hidden objects, selection and the view come back and
    the temporary copies are deleted - also on an error (finally), with undo
    off. In a camera view the shots are taken in a user perspective view and
    the camera view is restored, so the camera itself never moves."""
    out = {}
    if not mats or not objs:
        return out
    try:
        mxs.execute(_MXS_HELPERS)
        vcam = mxs.viewport.getCamera()
        vtm, vfov, vtype = mxs.viewport.getTM(), mxs.viewport.getFOV(), mxs.viewport.getType()
    except Exception as e:
        print("  (no part shots: %s)" % e)
        return out
    parts = _parts_of(mats, objs)
    total, zmin, zmax = 0, None, None
    for o in objs:
        try:
            total += int(mxs.ivoMR_tris(o))
            zmin = o.min.z if zmin is None else min(zmin, o.min.z)
            zmax = o.max.z if zmax is None else max(zmax, o.max.z)
        except Exception:
            pass
    tmp = os.path.join(os.environ.get("TEMP", "."), "_ivo_matrenamer_part.png")
    hidden = [(o, bool(o.isHidden)) for o in mxs.objects]
    sel = list(mxs.selection)
    temps = []
    with pymxs.undo(False):
        try:
            if vcam is not None:
                mxs.viewport.setType(mxs.name("view_persp_user"))
            for o, _ in hidden:
                o.isHidden = True
            for m in mats:
                if sub_mtls(m):
                    continue            # a container gets its name from its parts
                temps = []
                for o, mid in parts.get(_handle(m), []):
                    n = mxs.ivoMR_makePart(o, mid, o.material)
                    if n is not None:
                        temps.append(n)
                if not temps:
                    continue
                faces = sum(int(mxs.ivoMR_tris(n)) for n in temps)
                box = (min(n.min.x for n in temps), min(n.min.y for n in temps),
                       min(n.min.z for n in temps), max(n.max.x for n in temps),
                       max(n.max.y for n in temps), max(n.max.z for n in temps))
                zone = ""
                if zmin is not None and zmax > zmin:
                    lo, hi = (box[2] - zmin) / (zmax - zmin), (box[5] - zmin) / (zmax - zmin)
                    mid = (lo + hi) / 2.0
                    zone = "%s (height %.0f-%.0f%% of the model)" % (
                        "top" if mid > 0.66 else ("bottom" if mid < 0.33 else "middle"),
                        100 * lo, 100 * hi)
                mxs.select(mxs.Array(*temps))
                mxs.execute("max zoomext sel")
                img = None
                try:
                    img = _grab(tmp)
                except Exception as e:
                    print("  (part shot failed: %s)" % e)
                out[_handle(m)] = {"img": img, "faces": faces, "box": box, "zone": zone,
                                   "pct": (100.0 * faces / total) if total else 0}
                for n in temps:
                    mxs.delete(n)
                temps = []
        finally:
            for n in temps:
                try:
                    mxs.delete(n)
                except Exception:
                    pass
            for o, h in hidden:
                try:
                    o.isHidden = h
                except Exception:
                    pass
            try:
                if sel:
                    mxs.select(mxs.Array(*sel))
                else:
                    mxs.clearSelection()
                if vcam is not None:
                    mxs.viewport.setCamera(vcam)
                else:
                    mxs.viewport.setType(vtype)
                    mxs.viewport.setTM(vtm)
                    mxs.viewport.setFOV(vfov)
                mxs.completeRedraw()
            except Exception as e:
                print("  (the view could not be fully restored: %s)" % e)
    return out


def inside_of(shots, order):
    """{handle: number of the part whose bounding box holds this whole part}.
    A liquid in a bottle or a bulb in a lamp looks just like its shell."""
    out = {}
    for h, s in shots.items():
        a = s.get("box")
        if not a:
            continue
        best = None
        for k, h2 in enumerate(order, start=1):
            b = shots.get(h2, {}).get("box")
            if h2 == h or not b:
                continue
            tol = 0.01 * max(b[3] - b[0], b[4] - b[1], b[5] - b[2], 1e-6)
            if all(a[i] >= b[i] - tol for i in range(3)) and all(a[i] <= b[i] + tol for i in range(3, 6)):
                vol_b = (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2])
                vol_a = (a[3] - a[0]) * (a[4] - a[1]) * (a[5] - a[2])
                if vol_b > vol_a * 1.02 and (best is None or vol_b < best[1]):
                    best = (k, vol_b)
        if best:
            out[h] = best[0]
    return out


#: Properties that tell what a material is (glass, liquid, metal ...).
_PROPS = [
    ("refraction", "refraction (VRay)"), ("refraction_ior", "IOR"),
    ("refraction_fogColor", "fog colour"), ("refraction_fogMult", "fog mult"),
    ("reflection_glossiness", "reflection glossiness"), ("metalness", "metalness"),
    ("opacity", "opacity"), ("transparency", "transparency (Physical)"),
    ("trans_ior", "IOR (Physical)"), ("roughness", "roughness"),
    ("refractionLevel", "refraction (Corona)"), ("ior", "IOR (Corona)"),
    ("absorptionColor", "absorption colour"), ("baseLevel", "base level"),
]


def material_props(m):
    out = []
    for prop, label in _PROPS:
        try:
            v = mxs.getProperty(m, prop)
        except Exception:
            continue
        if v is None:
            continue
        try:
            if hasattr(v, "r"):
                v = "rgb %d,%d,%d" % (int(v.r), int(v.g), int(v.b))
            elif isinstance(v, float):
                v = "%.3g" % v
            out.append("%s %s" % (label, v))
        except Exception:
            pass
    return ", ".join(out)


# ---------------- collecting textures (optional) ----------------

def _texture_nodes(mats):
    """{abs path key: [abs path, [map nodes]]} for every image the materials use,
    plus the names of images that could not be found on disk."""
    by_file, missing = {}, []
    for m in mats:
        for _, tex in _texmap_props(m):
            leaves = []
            _leaf_bitmaps(tex, leaves)
            for b in leaves:
                raw = _bmp_path(b)
                ap = resolve_path(raw)
                if not ap:
                    missing.append(os.path.basename(raw or "?"))
                    continue
                key = os.path.normcase(os.path.normpath(ap))
                entry = by_file.setdefault(key, [os.path.normpath(ap), []])
                if b not in entry[1]:
                    entry[1].append(b)
    return by_file, sorted(set(missing))


# Renaming touches only copies this tool made for this scene. Which files those
# are is kept next to them, so an original - even one inside the scene folder,
# shared with another scene - is never renamed.
MANIFEST = ".ivo_material_renamer.json"


def _maps_dir():
    return os.path.join(str(mxs.maxFilePath or ""), "maps")


def _scene_name():
    return str(mxs.maxFileName or "")


def load_owned(maps):
    """{lower-case file name in maps: scene file that owns the copy}"""
    p = os.path.join(maps, MANIFEST)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return dict(json.load(f).get("files") or {})
    except Exception as e:
        print("  (cannot read %s: %s - no file counts as a copy of this tool)" % (p, e))
        return {}


def save_owned(maps, owned):
    p = os.path.join(maps, MANIFEST)
    try:
        os.makedirs(maps, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"tool": TITLE, "files": owned}, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("  (cannot write %s: %s)" % (p, e))


def owned_here(path, owned, maps):
    """True if path is a copy this tool made in maps for the open scene."""
    return (os.path.normcase(os.path.dirname(os.path.normpath(path))) == os.path.normcase(maps)
            and owned.get(os.path.basename(path).lower()) == _scene_name())


def plan_collect(mats, include_inside=False):
    """
    Returns (plan, missing)
      plan    : list of (source, target, [map nodes], reuse)
      missing : names of images not found on disk
    Every image outside the scene folder - and with include_inside (needed for
    renaming) every image that is not already this tool's copy - gets a target
    in <scene>\\maps. A target never overwrites anything: another file with the
    same name gets _001; this scene's own identical copy from an earlier run is
    reused.
    """
    scene_dir = str(mxs.maxFilePath or "")
    maps = _maps_dir()
    owned = load_owned(maps)
    by_file, missing = _texture_nodes(mats)
    plan, used = [], set()
    for key, (src, nodes) in by_file.items():
        if owned_here(src, owned, maps):
            continue
        if not include_inside and _inside(src, scene_dir):
            continue
        name, ext = os.path.splitext(os.path.basename(src))
        target, n, reuse = os.path.join(maps, name + ext), 1, False
        while True:
            t = os.path.normcase(target)
            if t not in used and not os.path.exists(target):
                break
            if (t not in used and owned_here(target, owned, maps)
                    and filecmp.cmp(src, target, shallow=False)):
                reuse = True
                break
            target = os.path.join(maps, "%s_%03d%s" % (name, n, ext))
            n += 1
        used.add(os.path.normcase(target))
        plan.append((src, target, nodes, reuse))
    return plan, missing


def apply_collect(plan):
    """Copies (never moves) the images and points the maps at the copies."""
    ok, fail = 0, 0
    maps = _maps_dir()
    owned = load_owned(maps)
    for src, target, nodes, reuse in plan:
        if not reuse:
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    raise OSError("target appeared meanwhile, not overwriting")
                shutil.copy2(src, target)
            except Exception as e:
                print("   FAILED to copy %s : %s" % (src, e))
                fail += 1
                continue
            owned[os.path.basename(target).lower()] = _scene_name()
        for b in nodes:
            _set_bmp_path(b, target)
        print("   %s  ->  %s%s" % (src, target, "  (already there)" if reuse else ""))
        ok += 1
    save_owned(maps, owned)
    return ok, fail


# ---------------- texture file renaming (optional) ----------------

def plan_texture_renames(mats, final_names):
    """
    Returns (plan, skipped)
      plan    : list of (old_abs, new_abs, [bitmap nodes])
      skipped : list of "reason" strings
    Only this tool's own copies in <scene>\\maps are considered (see MANIFEST).
    """
    scene_dir = str(mxs.maxFilePath or "")
    plan, skipped = [], []
    if not scene_dir:
        skipped.append("Scene is not saved yet - no scene folder to work in.")
        return plan, skipped
    maps = _maps_dir()
    owned = load_owned(maps)

    by_file = {}          # abs path -> (proposed base name, [nodes])

    for m, newname in zip(mats, final_names):
        for slot, tex in _texmap_props(m):
            suffix = slot_suffix(slot)
            leaves = []
            _leaf_bitmaps(tex, leaves)
            for b in leaves:
                raw = _bmp_path(b)
                ap = resolve_path(raw)
                if not ap:
                    skipped.append("not found on disk: %s" % os.path.basename(raw or "?"))
                    continue
                key = os.path.normcase(os.path.normpath(ap))
                if key not in by_file:
                    by_file[key] = ["%s_%s" % (newname, suffix), [], os.path.normpath(ap)]
                by_file[key][1].append(b)

    used_targets = set()
    for base, nodes, abspath in by_file.values():
        # SAFETY: only copies this tool made for this scene - never an original
        if not owned_here(abspath, owned, maps):
            skipped.append("not a copy made by this tool: %s" % abspath)
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
    maps = _maps_dir()
    owned = load_owned(maps)
    try:
        mxs.freeSceneBitmaps()
    except Exception:
        pass
    for old, new, nodes in plan:
        try:
            if os.path.exists(new):
                raise OSError("target exists, not overwriting")
            os.rename(old, new)
        except Exception as e:
            print("   FAILED %s -> %s : %s" % (os.path.basename(old),
                                               os.path.basename(new), e))
            fail += 1
            continue
        owned.pop(os.path.basename(old).lower(), None)
        owned[os.path.basename(new).lower()] = _scene_name()
        for b in nodes:
            _set_bmp_path(b, new)
        print("   %s  ->  %s" % (os.path.basename(old), os.path.basename(new)))
        ok += 1
    save_owned(maps, owned)
    try:
        mxs.freeSceneBitmaps()
    except Exception:
        pass
    return ok, fail


# ---------------- API ----------------

def ask_claude(asset, items, vp):
    key = get_key()
    if not key:
        info("No API key found.\n\nOpen the tool and press 'Save Key'.")
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
        "Every material comes with a PICTURE OF ITS OWN PART - only the faces "
        "that use it, everything else hidden - plus how much of the model it "
        "covers, where it sits in height, and its key settings. Name it from "
        "that picture; the order of the list and texture names (often *_baked) "
        "tell nothing.\n\n"
        "Scene / asset: %s\n" % asset}]

    if vp:
        content.append({"type": "text", "text": "Here is the whole scene in the viewport:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": vp[0], "data": vp[1]}})

    for it in items:
        if it.get("parts") is not None:
            content.append({"type": "text", "text":
                "\nMaterial #%d | current name: %s | %s holding materials %s - "
                "name the whole object it is applied to (e.g. Sofa, Olive_Oil_Bottle)" % (
                    it["idx"], it["name"], it["cls"],
                    ", ".join("#%d" % p for p in it["parts"]) or "(none)")})
            continue
        head = "\nMaterial #%d | current name: %s | class: %s | diffuse RGB: %s | texture: %s" % (
            it["idx"], it["name"], it["cls"], it["rgb"],
            os.path.basename(it["tex"]) if it["tex"] else "(none)")
        part = it.get("part")
        if part:
            head += " | part: %d faces (%.1f%% of the model), %s" % (
                part["faces"], part["pct"], part["zone"] or "-")
        if it.get("inside"):
            head += " | the WHOLE part is inside part #%d (a volume inside it, e.g. a liquid)" % it["inside"]
        if it.get("props"):
            head += " | settings: %s" % it["props"]
        content.append({"type": "text", "text": head})
        if part and part.get("img"):
            content.append({"type": "text", "text": "Picture of the part with material #%d:" % it["idx"]})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": part["img"][0], "data": part["img"][1]}})
        if it.get("img"):
            content.append({"type": "text", "text": "Texture of material #%d:" % it["idx"]})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": it["img"][0], "data": it["img"][1]}})

    content.append({"type": "text", "text":
        "\n\nReturn ONLY a JSON array, no explanations, like:\n"
        '[{"idx": 1, "name": "Oyster_Shell"}, {"idx": 2, "name": "Bacon"}]'})

    # ~20 tokens per material; 1024 cut the list off in a scene with many
    # materials and the reply could not be read at all.
    payload = {"model": MODEL, "max_tokens": 4096,
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
        info("API error %s\n\n%s%s" % (e.code, body, hint))
        return None
    except Exception as e:
        info("Could not reach the API:\n%s" % e)
        return None

    txt = "".join(b.get("text", "") for b in data.get("content", []))
    stop = data.get("stop_reason")
    res = parse_names(txt)
    if stop == "max_tokens":
        print("The reply was cut off (max_tokens) after %d names." % len(res))
    if not res:
        print("Unexpected reply (stop_reason: %s):\n%s" % (stop, txt[:1500]))
        info("Unexpected reply from the API - see the Listener.\n\n"
             "stop_reason: %s\n%s" % (stop, txt[:300]))
        return None
    return res


def parse_names(txt):
    """[{"idx": n, "name": "..."}] from the reply. Read entry by entry, not as
    one JSON array, so a reply in a code fence, with extra text or cut off
    mid-list still gives every complete entry."""
    out = []
    for m in re.finditer(r'\{[^{}]*?"idx"\s*:\s*(\d+)[^{}]*?"name"\s*:\s*"((?:[^"\\]|\\.)*)"[^{}]*\}',
                         txt):
        try:
            out.append({"idx": int(m.group(1)), "name": json.loads('"%s"' % m.group(2))})
        except Exception as e:
            print("  (skipped an entry: %s)" % e)
    return out


# ---------------- main ----------------

def _collect_step(mats, for_rename=False):
    plan, missing = plan_collect(mats, include_inside=for_rename)
    for name in missing:
        print("   not found on disk: %s" % name)
    note = ("\n(%d texture(s) could not be found on disk.)" % len(missing)) if missing else ""
    if not plan:
        return ("\n\nAll textures are already this tool's copies in maps." if for_rename
                else "\n\nAll textures are already inside the scene folder.") + note, False
    maps = _maps_dir()
    preview = "\n".join("   %s%s" % (os.path.basename(s), "  (already in maps)" if r else "")
                        for s, _, _, r in plan)
    print("\n---- TEXTURES TO COLLECT ----\n" + preview)
    why = ("Only these copies get renamed - the originals are never touched,\n"
           "so other scenes that use them keep working." if for_rename
           else "The originals are not touched.")
    if not ask("Copy %d texture file(s) into\n%s ?\n\n%s\n\n%s" % (len(plan), maps, preview, why)):
        print(">>> Collecting cancelled. <<<")
        return "\n\nTextures were not collected." + note, False
    ok, fail = apply_collect(plan)
    msg = "\n\nTextures collected into maps: %d" % ok
    if fail:
        msg += " (%d failed - see Listener)" % fail
    return msg + note, ok > 0


def _rename_step(mats, finals):
    _FILES.clear()   # collecting may have just added files
    plan, skipped = plan_texture_renames(mats, finals)
    for s in skipped:
        print("   skipped: %s" % s)
    lost = len({s for s in skipped if s.startswith("not found")})
    note = ("\n(%d texture(s) could not be found on disk - see the Listener.)" % lost) if lost else ""
    originals = len([s for s in skipped if s.startswith("not a copy")])
    if originals:
        note += ("\n(%d original texture(s) were left alone - only copies in maps\n"
                 "are renamed.)" % originals)
    if not plan:
        return "\n\nNo texture files needed renaming." + note, False
    preview = "\n".join(
        "   %s   ->   %s" % (os.path.basename(o), os.path.basename(n))
        for o, n, _ in plan)
    print("\n---- TEXTURE FILES TO RENAME ----\n" + preview)
    if not ask("Rename these texture FILES on disk?\n\n" + preview +
               "\n\n(Only this tool's copies in the scene's maps folder.)"):
        print(">>> Texture renaming cancelled. <<<")
        return "\n\nTexture files left unchanged.", False
    ok, fail = apply_texture_renames(plan)
    msg = "\n\nTexture files renamed: %d" % ok
    if fail:
        msg += " (%d failed - see Listener)" % fail
    return msg + note, ok > 0


def _this_file():
    try:
        return os.path.abspath(__file__)
    except NameError:
        return "?"


def run():
    if Image is None:
        info("Python module 'Pillow' is missing.\n\n"
             "Open a terminal in:\n"
             "  <3ds Max install folder>\\Python\n"
             "and run:\n"
             "  .\\python.exe -m pip install --user pillow")
        return

    _FILES.clear()
    # The window (.ms) writes its version; a mismatch means an old copy runs.
    ms_ver = _ini_value("Version", "?")
    print("\n==== %s  (%s, window %s) ====" % (TITLE, _this_file(), ms_ver))
    if ms_ver != VERSION:
        info("The window (.ms) is version %s, this script (.py) is %s.\n\n"
             "Keep both files of the same version in one folder and run the .ms\n"
             "from there (a toolbar button may still point at an old copy).\n\n%s"
             % (ms_ver, VERSION, _this_file()))
        return
    scope = get_scope()
    print("  scope=%s  collect=%s  rename=%s  scene folder=%s" % (
        scope, collect_textures_enabled(), rename_textures_enabled(), mxs.maxFilePath or "-"))
    mats = collect_materials(scope)
    if not mats:
        if scope == 2:
            info("No materials found on scene objects.")
        return

    asset = mxs.maxFileName or "untitled scene"
    asset = re.sub(r"\.max$", "", asset, flags=re.I)

    print("\n---- Ivo Material Renamer: collecting %d materials ----" % len(mats))
    items = []
    multis = multi_parts(mats)
    for i, m in enumerate(mats, start=1):
        tex = None if i in multis else resolve_path(tex_of(m))
        items.append({"idx": i, "name": str(m.name), "cls": str(mxs.classOf(m)),
                      "rgb": diffuse_rgb(m), "tex": tex or "",
                      "img": img_b64(tex) if tex else None, "parts": multis.get(i)})
        print("  [%d] %s   tex=%s" % (i, m.name,
                                      os.path.basename(tex) if tex else "-"))

    vp = viewport_shot()
    print("Taking a picture of every part...")
    objs = list(mxs.geometry) if scope == 2 else list(mxs.selection)
    shots = part_shots(mats, objs)
    inside = inside_of(shots, [_handle(m) for m in mats])
    for it, m in zip(items, mats):
        it["part"] = shots.get(_handle(m))
        it["inside"] = inside.get(_handle(m))
        it["props"] = "" if it.get("parts") is not None else material_props(m)
        p = it["part"]
        if p:
            print("  [%d] %d faces, %.1f%%, %s%s" % (it["idx"], p["faces"], p["pct"], p["zone"],
                  ("  - inside part #%d" % it["inside"]) if it["inside"] else ""))
    batches = make_batches(items)
    res = []
    for n, batch in enumerate(batches, start=1):
        print("Asking Claude... (%d of %d)" % (n, len(batches)) if len(batches) > 1
              else "Asking Claude...")
        part = ask_claude(asset, batch, vp)
        if not part:
            # the error is already shown; nothing has been renamed yet
            print(">>> Stopped - nothing was changed. <<<")
            return
        res.extend(part)

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

    if not ask("Claude proposes these names:\n\n" + report + "\n\nApply them?"):
        print(">>> Cancelled - nothing was changed. <<<")
        return

    for m, f in zip(mats, finals):
        m.name = f
    print(">>> Names applied. <<<")

    # ---- optional: collect the textures, then rename the files ----
    tex_msg = ""
    collect, rename = collect_textures_enabled(), rename_textures_enabled()
    if collect or rename:
        if scope != 1:
            tex_msg = ("\n\nTexture files were NOT touched:\n"
                       "that only works in 'Only on SELECTED objects' mode.")
            print(">>> Texture files skipped (selection mode only). <<<")
        elif not str(mxs.maxFilePath or ""):
            tex_msg = ("\n\nTexture files were NOT touched:\n"
                       "the scene is not saved, so it has no folder yet.")
            print(">>> Texture files skipped (scene not saved). <<<")
        else:
            changed = False
            if collect or rename:
                # renaming works only on copies, so it always collects first
                msg, ch = _collect_step(mats, for_rename=rename)
                tex_msg += msg
                changed = changed or ch
            if rename:
                msg, ch = _rename_step(mats, finals)
                tex_msg += msg
                changed = changed or ch
            if changed:
                tex_msg += "\n\nSave the scene to keep the new paths."
    else:
        tex_msg = "\n\nTexture files: not touched (both texture options were off)."

    info("Renamed %d materials." % len(mats) + tex_msg)


if not os.environ.get("IVO_MATRENAMER_TEST"):
    run()
