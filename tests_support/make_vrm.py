"""
A tiny but real VRM 1.0 model, for tests: a skeleton with every required
humanoid bone, a coloured head with two eyes, and morph-target expressions
(happy, sad, surprised, relaxed, blink, aa…), so three-vrm loads it, maps its
expressions, lip-syncs and renders it like any downloaded model.
"""

import json
import struct
from typing import Dict, List, Tuple

BONES = [  # name, parent, position (relative)
    ("hips", None, (0, 0.9, 0)), ("spine", "hips", (0, 0.12, 0)), ("chest", "spine", (0, 0.15, 0)),
    ("neck", "chest", (0, 0.18, 0)), ("head", "neck", (0, 0.08, 0)),
    ("leftUpperArm", "chest", (0.18, 0.12, 0)), ("leftLowerArm", "leftUpperArm", (0.25, 0, 0)),
    ("leftHand", "leftLowerArm", (0.22, 0, 0)),
    ("rightUpperArm", "chest", (-0.18, 0.12, 0)), ("rightLowerArm", "rightUpperArm", (-0.25, 0, 0)),
    ("rightHand", "rightLowerArm", (-0.22, 0, 0)),
    ("leftUpperLeg", "hips", (0.09, -0.05, 0)), ("leftLowerLeg", "leftUpperLeg", (0, -0.4, 0)),
    ("leftFoot", "leftLowerLeg", (0, -0.4, 0)),
    ("rightUpperLeg", "hips", (-0.09, -0.05, 0)), ("rightLowerLeg", "rightUpperLeg", (0, -0.4, 0)),
    ("rightFoot", "rightLowerLeg", (0, -0.4, 0)),
]
EXPRESSIONS = ("happy", "sad", "surprised", "relaxed", "angry", "blink", "blinkLeft", "blinkRight",
               "aa", "ih", "ou", "ee", "oh")


def _box(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float) -> Tuple[List[float], List[int]]:
    corners = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    positions = [v for x, y, z in corners for v in (cx + x * sx, cy + y * sy, cz + z * sz)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
             (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return positions, [i for face in faces for i in face]


def build() -> bytes:
    # Head (face toward +Z is fine: VRM 1.0 faces +Z) plus two eyes and a mouth.
    parts = [_box(0, 0.1, 0, 0.11, 0.12, 0.1), _box(0.04, 0.12, 0.1, 0.018, 0.022, 0.01),
             _box(-0.04, 0.12, 0.1, 0.018, 0.022, 0.01), _box(0, 0.04, 0.1, 0.03, 0.008, 0.01)]
    positions: List[float] = []
    indices: List[int] = []
    for pos, idx in parts:
        base = len(positions) // 3
        positions += pos
        indices += [base + i for i in idx]
    count = len(positions) // 3

    def morph(kind: str) -> List[float]:
        delta = [0.0] * len(positions)
        for v in range(count):
            part = v // 8
            if kind in ("blink", "blinkLeft", "blinkRight") and part in (1, 2):
                if kind == "blink" or (kind == "blinkLeft" and part == 1) or (kind == "blinkRight" and part == 2):
                    delta[v * 3 + 1] = (0.12 - positions[v * 3 + 1]) * 0.9
            elif part == 3:
                y_sign = 1 if positions[v * 3 + 1] > 0.04 else -1
                amount = {"aa": 0.03, "oh": 0.025, "ou": 0.015, "ih": 0.01, "ee": 0.008, "surprised": 0.03,
                          "happy": 0.006, "sad": -0.006, "angry": -0.004, "relaxed": 0.004}.get(kind, 0)
                delta[v * 3 + 1] = -y_sign * amount if kind not in ("happy", "relaxed") else amount
        return delta

    targets = [morph(name) for name in EXPRESSIONS]
    blob = bytearray()
    views, accessors = [], []

    def add(data: bytes, target=None) -> int:
        while len(blob) % 4:
            blob.append(0)
        view = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target:
            view["target"] = target
        views.append(view)
        blob.extend(data)
        return len(views) - 1

    def vec3(values: List[float], with_bounds=True) -> int:
        view = add(struct.pack(f"<{len(values)}f", *values), 34962)
        acc = {"bufferView": view, "componentType": 5126, "count": len(values) // 3, "type": "VEC3"}
        if with_bounds:
            acc["min"] = [min(values[i::3]) for i in range(3)]
            acc["max"] = [max(values[i::3]) for i in range(3)]
        accessors.append(acc)
        return len(accessors) - 1

    pos_acc = vec3(positions)
    index_view = add(struct.pack(f"<{len(indices)}H", *indices), 34963)
    accessors.append({"bufferView": index_view, "componentType": 5123, "count": len(indices), "type": "SCALAR"})
    index_acc = len(accessors) - 1
    target_accs = [vec3(t) for t in targets]

    nodes: List[Dict] = []
    names = [b[0] for b in BONES]
    for name, parent, pos in BONES:
        nodes.append({"name": name, "translation": list(pos)})
    for i, (name, parent, _) in enumerate(BONES):
        if parent:
            nodes[names.index(parent)].setdefault("children", []).append(i)
    head_mesh_node = len(nodes)
    nodes.append({"name": "HeadMesh", "mesh": 0})
    nodes[names.index("head")].setdefault("children", []).append(head_mesh_node)

    gltf = {
        "asset": {"version": "2.0", "generator": "cerebro-test"},
        "extensionsUsed": ["VRMC_vrm"],
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes,
        "materials": [{"name": "skin", "pbrMetallicRoughness": {"baseColorFactor": [1, 0.82, 0.86, 1],
                                                                "metallicFactor": 0, "roughnessFactor": 1}}],
        "meshes": [{"name": "Head", "primitives": [{"attributes": {"POSITION": pos_acc}, "indices": index_acc,
                                                    "material": 0,
                                                    "targets": [{"POSITION": a} for a in target_accs]}],
                    "weights": [0.0] * len(EXPRESSIONS),
                    "extras": {"targetNames": list(EXPRESSIONS)}}],
        "buffers": [{"byteLength": 0}], "bufferViews": views, "accessors": accessors,
        "extensions": {"VRMC_vrm": {
            "specVersion": "1.0",
            "meta": {"name": "Cerebro test model", "version": "1", "authors": ["Cerebro tests"],
                     "licenseUrl": "https://vrm.dev/licenses/1.0/",
                     "avatarPermission": "everyone", "commercialUsage": "corporation",
                     "allowRedistribution": True, "modification": "allowModificationRedistribution"},
            "humanoid": {"humanBones": {name: {"node": i} for i, name in enumerate(names)}},
            "expressions": {"preset": {name: {"morphTargetBinds": [{"node": head_mesh_node, "index": i, "weight": 1.0}]}
                                       for i, name in enumerate(EXPRESSIONS)}},
        }},
    }
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"][0]["byteLength"] = len(blob)
    text = json.dumps(gltf, separators=(",", ":")).encode()
    while len(text) % 4:
        text += b" "
    body = (struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body
