"""
Resolution of the 3D teaching model shown beside an answer.

This used to live inline in the chat endpoint and had three structural
problems, all of which the student could see:

1. Tripo3D text-to-3D was tried FIRST for every physical object. Generated
   meshes are untextured white blobs with no internal parts — a "human heart"
   came out as a smooth lump — while Sketchfab had real, textured, scanned
   anatomy sitting in the fallback branch that almost never ran.
2. The polling loop used `time.sleep` and `urllib` inside an `async` endpoint,
   so one 3D request froze the whole worker for up to 30 seconds — for every
   concurrent student, not just the one who asked.
3. Sketchfab download URLs expire in 300 seconds (measured, not guessed), but
   the resolved artifact is persisted in the browser's localStorage. Every 3D
   model in a student's history was therefore dead five minutes after it was
   generated.

The order is now curated -> search -> generate, everything is async, and every
mesh is copied into a local cache and served from a stable URL that does not
expire.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
import urllib.request

# Anything larger stalls a mid-range phone for a minute and usually is not a
# teaching model at all but a photogrammetry scan. Sketchfab reports the GLB
# size before download, so most of these are rejected without a byte fetched.
MAX_MODEL_BYTES = 25 * 1024 * 1024

_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# Resolved widgets, keyed by normalized query. A class of 40 students asking
# "explain the human heart" should hit Sketchfab once, not 40 times.
_RESOLVED: Dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════
#  Curated models
#
#  Sketchfab is an asset marketplace, so its relevance ranking is commercial,
#  not educational. Verified against the live API while writing this:
#    "moon"           -> "Moon Shoes"
#    "atom"           -> "Real Steel HD Original Atom Model" (a film robot)
#    "leaf structure" -> a Baroque wooden coffee table
#  No scoring heuristic saves you from that reliably, so the topics NCERT
#  actually asks about are pinned to models that were checked by hand.
#
#  Every entry is verified by scripts/curate_models.py, which downloads the
#  head of each candidate GLB and counts materials that actually carry a
#  base-colour texture. Ranking on search metadata alone is not enough: it
#  says nothing about textures, and the first pass pinned 16 untextured meshes
#  that rendered as flat clay next to the textured heart. A handful of topics
#  (skull, neuron, DNA) have no textured model in the free downloadable pool
#  and stay flat — the viewer tints those rather than leaving them white.
# ══════════════════════════════════════════════════════════════════════════

CURATED_MODELS: Dict[str, str] = {
    # Human body
    "human heart": "3f8072336ce94d18b3d0d055a1ece089",
    "human brain": "c9c9d4d671b94345952d012cc2ea7a24",
    "human eye": "b68ae822b45443edb812950993ece362",
    "human ear": "471e5ab2ad9c41279d4d4ec5cdc630cc",
    "human kidney": "e1476ceb1e3b4412af5418eee9c5ed08",
    "human lungs": "ce09f4099a68467880f46e61eb9a3531",
    "human liver": "22fed92c33374eac8695b1e531b34f0a",
    "human stomach": "56ffcd2330ae4b7ea6c7b8a08c82b4b7",
    "nephron": "c945a7258923402ea1d10fe9463840cc",
    "human skeleton": "b8e05af2837d483190d196928b2bd9dc",
    "human skull": "f1eaaef50e5845c796d6834fd1b702e5",
    "rib cage": "1aa94c70fc4f40cda0c4ef23c0f44d03",
    "spinal cord": "c3946cc014d24b02a3224565108907ba",
    "hand bones": "1704c8bec5db422fbe17c1e9650c293a",
    "human tooth": "e5ddce218ebc4d16b45b057a418c0c56",
    "digestive system": "f78ce703805f49d3b732e37be5b93188",
    # Cell biology
    "neuron": "d40557a1e4154267b78117433bc51296",
    "animal cell": "abaa9a651c834cdaa67072b32fb0024f",
    "plant cell": "0640c7a14f41400fbdac382c7de1d776",
    "mitochondria": "7445a425050e49daa881070ca6917a91",
    "chloroplast": "30bc55b2f763415f9222f876c564be97",
    "dna": "6749915e7e1645a18b74a676d4f96eab",
    "amoeba": "cfad65ff5d0d4a9a80d8c4842a0d9a57",
    "paramecium": "5608f0c1f25c45fdb96d88c596570c76",
    # Physics / chemistry / astronomy
    "atom": "6a283d5b19c34e2b8fcfc6907b231aea",
    "earth": "babd284930204736a938915ceb0a6535",
    "moon": "fc1e78cfc65549c6a49e88ba599b7901",
    "solar system": "1d790c7e712f4500b417e9c13639e79f",
    "volcano": "b081e30676764ffeab31eaea47af0ba6",
    "electric motor": "048b9dc3095e44c0883b6b7944ad4916",
    "microscope": "20f74d0ddc3b458ea4bc3bd05fb1cf43",
    # Physics apparatus. Only picks whose title unambiguously names the real
    # object are pinned: the free pool is thick with game props, and the
    # automated pass happily proposed "Rocket from Guardians Of The Galaxy"
    # for rocket and a "Magical Energy Stone" for prism.
    "electric generator": "e6e0f270e03345a5b3e02a2ddea91b44",
    "telescope": "7220b6283360440fbbf8ccff08e25a7f",
    "wind turbine": "4feb15af7b554c16b88bf9cbe179558b",
    "pendulum": "48eddd13919846bcbe275accb986b1bf",
    "solar panel": "0f444052d1e64ccfb065fb8d00405f19",
    "pulley": "52d53987007648d0b4c76a33f680f462",
}

# What a student may type -> the curated key. Kept deliberately literal; a
# fuzzy matcher here would reintroduce exactly the mis-routing this table exists
# to prevent.
_ALIASES: Dict[str, str] = {
    "heart": "human heart", "heart structure": "human heart",
    "human heart structure": "human heart", "mammalian heart": "human heart",
    "brain": "human brain", "human brain structure": "human brain",
    "eye": "human eye", "human eye structure": "human eye", "eyeball": "human eye",
    "ear": "human ear", "kidney": "human kidney", "kidneys": "human kidney",
    "lungs": "human lungs", "lung": "human lungs",
    "liver": "human liver", "stomach": "human stomach",
    "skeleton": "human skeleton", "human skeletal system": "human skeleton",
    "skull": "human skull", "ribcage": "rib cage", "ribs": "rib cage",
    "tooth": "human tooth", "teeth": "human tooth",
    "alimentary canal": "digestive system",
    "human digestive system": "digestive system",
    "nerve cell": "neuron", "neurone": "neuron",
    "cell": "animal cell", "eukaryotic cell": "animal cell",
    "dna double helix": "dna", "dna structure": "dna",
    "structure of atom": "atom", "atomic structure": "atom", "atom model": "atom",
    "planet earth": "earth", "the moon": "moon",
    "solar system planets": "solar system", "sun and planets": "solar system",
    "compound microscope": "microscope", "dc motor": "electric motor",
    "generator": "electric generator", "ac generator": "electric generator",
    "dynamo": "electric generator", "electric dynamo": "electric generator",
    "windmill": "wind turbine", "simple pendulum": "pendulum",
    "solar cell": "solar panel", "photovoltaic cell": "solar panel",
    "pulley system": "pulley", "block and tackle": "pulley",
    "refracting telescope": "telescope", "astronomical telescope": "telescope",
    "electric dc motor": "electric motor",
}


# ══════════════════════════════════════════════════════════════════════════
#  Curated part labels
#
#  The answer model is good at naming NCERT parts and bad at knowing which of
#  them a downloaded mesh actually shows, so for the topics above the labels
#  are fixed here. `color` is a real anatomical/diagram convention where one
#  exists (oxygen-poor blood blue, oxygen-rich red) — this is what turns the
#  viewer from a grey shape into a diagram a student can read.
# ══════════════════════════════════════════════════════════════════════════

CURATED_LABELS: Dict[str, List[dict]] = {
    # ── Physics ───────────────────────────────────────────────────────────
    "electric motor": [
        {"name": "Armature coil", "note": "current-carrying coil that spins when the magnetic field pushes on it", "anchor": "center"},
        {"name": "Split-ring commutator", "note": "reverses the current every half turn so the coil keeps rotating one way", "anchor": "right"},
        {"name": "Carbon brushes", "note": "press on the commutator to feed current into the spinning coil", "anchor": "right-bottom"},
        {"name": "Field magnet", "note": "supplies the magnetic field the coil pushes against", "anchor": "left"},
        {"name": "Axle", "note": "carries the rotation out of the motor to do useful work", "anchor": "top"},
    ],
    "electric generator": [
        {"name": "Armature coil", "note": "rotating coil in which a current is induced as it cuts field lines", "anchor": "center"},
        {"name": "Slip rings", "note": "keep the rotating coil connected to the external circuit", "anchor": "right"},
        {"name": "Brushes", "note": "carry the induced current out to the circuit", "anchor": "right-bottom"},
        {"name": "Field magnet", "note": "provides the magnetic field needed for induction", "anchor": "left"},
    ],
    "transformer": [
        {"name": "Primary coil", "note": "input winding; alternating current here makes a changing magnetic flux", "anchor": "left"},
        {"name": "Secondary coil", "note": "output winding; the changing flux induces a voltage across it", "anchor": "right"},
        {"name": "Soft iron core", "note": "carries the magnetic flux from one coil to the other with little loss", "anchor": "center"},
    ],
    "convex lens": [
        {"name": "Optical centre", "note": "central point; a ray through it passes on undeviated", "anchor": "center"},
        {"name": "Principal focus", "note": "where rays parallel to the principal axis meet after refraction", "anchor": "right"},
        {"name": "Curved surface", "note": "bends light inward because the lens is thicker at the middle", "anchor": "top"},
        {"name": "Principal axis", "note": "line through the optical centre and the centres of curvature", "anchor": "left"},
    ],
    "prism": [
        {"name": "Refracting surface", "note": "light bends here on entering and again on leaving the glass", "anchor": "front"},
        {"name": "Angle of prism", "note": "angle between the two refracting faces; controls how much light bends", "anchor": "top"},
        {"name": "Base", "note": "the spectrum is always bent towards this thicker side", "anchor": "bottom"},
    ],
    "pulley": [
        {"name": "Grooved wheel", "note": "turns freely so the rope changes direction without much friction", "anchor": "top"},
        {"name": "Axle", "note": "fixed rod the wheel turns about", "anchor": "center"},
        {"name": "Rope", "note": "transmits the effort to the load around the groove", "anchor": "left"},
        {"name": "Load", "note": "the weight being raised by the machine", "anchor": "bottom"},
    ],
    "lever": [
        {"name": "Fulcrum", "note": "fixed point the rigid bar turns about", "anchor": "center"},
        {"name": "Effort arm", "note": "distance from the fulcrum to where the effort is applied", "anchor": "right"},
        {"name": "Load arm", "note": "distance from the fulcrum to the load being moved", "anchor": "left"},
        {"name": "Load", "note": "the resistance or weight the lever has to overcome", "anchor": "left-bottom"},
    ],
    "telescope": [
        {"name": "Objective lens", "note": "large lens that gathers light and forms an image of a distant object", "anchor": "front"},
        {"name": "Eyepiece", "note": "small lens that magnifies the image formed by the objective", "anchor": "back"},
        {"name": "Tube", "note": "holds the lenses the correct distance apart and blocks stray light", "anchor": "center"},
        {"name": "Mount", "note": "supports the tube and lets it be turned towards any part of the sky", "anchor": "bottom"},
    ],
    "bar magnet": [
        {"name": "North pole", "note": "field lines leave the magnet here; points north when freely suspended", "anchor": "right", "color": "#D64545"},
        {"name": "South pole", "note": "field lines re-enter the magnet here", "anchor": "left", "color": "#3B7DD8"},
        {"name": "Neutral region", "note": "middle of the magnet, where the attracting power is weakest", "anchor": "center"},
    ],
    "wind turbine": [
        {"name": "Rotor blades", "note": "shaped like aerofoils so moving air makes them turn", "anchor": "front"},
        {"name": "Nacelle", "note": "housing holding the gearbox and generator at the top of the tower", "anchor": "center"},
        {"name": "Tower", "note": "raises the blades to where the wind is stronger and steadier", "anchor": "bottom"},
        {"name": "Hub", "note": "joins the blades to the shaft that drives the generator", "anchor": "front-top"},
    ],
    "pendulum": [
        {"name": "Bob", "note": "heavy mass whose to-and-fro swing is the oscillation", "anchor": "bottom"},
        {"name": "String", "note": "its length decides the time period of the swing", "anchor": "center"},
        {"name": "Point of suspension", "note": "fixed pivot the pendulum swings about", "anchor": "top"},
    ],
    "rocket": [
        {"name": "Payload", "note": "satellite or spacecraft the rocket is carrying to orbit", "anchor": "top"},
        {"name": "Fuel tank", "note": "stores the propellant and the oxidiser burnt during flight", "anchor": "center"},
        {"name": "Combustion chamber", "note": "fuel burns here, producing hot high-pressure gas", "anchor": "bottom"},
        {"name": "Nozzle", "note": "gas escapes downward, and the reaction force pushes the rocket up", "anchor": "bottom-back"},
        {"name": "Fins", "note": "keep the rocket flying straight and stable", "anchor": "bottom-front"},
    ],
    "satellite": [
        {"name": "Solar panels", "note": "convert sunlight into the electricity that powers the satellite", "anchor": "left"},
        {"name": "Antenna", "note": "sends data down to Earth and receives commands from the ground", "anchor": "bottom"},
        {"name": "Main body", "note": "carries the instruments, batteries and control systems", "anchor": "center"},
    ],
    "microscope": [
        {"name": "Eyepiece", "note": "lens you look through; magnifies the image again", "anchor": "top"},
        {"name": "Objective lens", "note": "close to the slide; forms the first magnified image", "anchor": "center"},
        {"name": "Stage", "note": "flat platform where the slide is clipped in place", "anchor": "front"},
        {"name": "Mirror", "note": "reflects light up through the specimen so it can be seen", "anchor": "bottom"},
    ],

    # ── Chemistry ─────────────────────────────────────────────────────────
    "sodium chloride crystal": [
        {"name": "Sodium ion", "note": "Na+, formed when a sodium atom loses one electron", "anchor": "left", "color": "#8B5CF6"},
        {"name": "Chloride ion", "note": "Cl-, formed when a chlorine atom gains that electron", "anchor": "right", "color": "#3E8E5A"},
        {"name": "Ionic bond", "note": "strong attraction between oppositely charged ions holding the lattice together", "anchor": "center"},
        {"name": "Cubic lattice", "note": "each ion is surrounded by six of the opposite kind", "anchor": "top"},
    ],
    "diamond": [
        {"name": "Carbon atom", "note": "each one is bonded to four others", "anchor": "center"},
        {"name": "Covalent bond", "note": "strong shared-electron bonds in every direction make diamond the hardest natural substance", "anchor": "right"},
        {"name": "Tetrahedral structure", "note": "rigid three-dimensional network with no free electrons, so it does not conduct", "anchor": "top"},
    ],
    "graphite": [
        {"name": "Hexagonal layer", "note": "each carbon is bonded to only three others, forming flat sheets", "anchor": "center"},
        {"name": "Weak forces", "note": "layers slide over each other easily, which is why graphite marks paper", "anchor": "left"},
        {"name": "Free electron", "note": "the fourth electron is delocalised, so graphite conducts electricity", "anchor": "top"},
    ],

    # ── Biology ───────────────────────────────────────────────────────────
    "flower": [
        {"name": "Petal", "note": "brightly coloured to attract insects and birds for pollination", "anchor": "front"},
        {"name": "Sepal", "note": "green outer part that protected the flower while it was a bud", "anchor": "bottom"},
        {"name": "Stamen", "note": "male part; the anther makes pollen grains", "anchor": "right"},
        {"name": "Pistil", "note": "female part; pollen lands on the sticky stigma", "anchor": "center"},
        {"name": "Ovary", "note": "holds the ovules and becomes the fruit after fertilisation", "anchor": "bottom-center"},
    ],
    "leaf": [
        {"name": "Lamina", "note": "broad flat blade that catches sunlight for photosynthesis", "anchor": "center"},
        {"name": "Midrib", "note": "thick central vein that supports the blade", "anchor": "center"},
        {"name": "Veins", "note": "carry water to the leaf and food away from it", "anchor": "right"},
        {"name": "Petiole", "note": "stalk joining the leaf to the stem", "anchor": "bottom"},
    ],
    "human ear": [
        {"name": "Pinna", "note": "outer flap that collects sound waves and directs them inward", "anchor": "front"},
        {"name": "Ear canal", "note": "tube carrying the sound waves to the eardrum", "anchor": "center"},
        {"name": "Eardrum", "note": "thin membrane that vibrates when sound waves strike it", "anchor": "back"},
        {"name": "Cochlea", "note": "coiled fluid-filled tube that converts vibrations into nerve impulses", "anchor": "back-bottom"},
    ],
    "human tooth": [
        {"name": "Enamel", "note": "hardest substance in the body, protecting the crown", "anchor": "top"},
        {"name": "Dentine", "note": "bone-like layer beneath the enamel that makes up most of the tooth", "anchor": "center"},
        {"name": "Pulp cavity", "note": "holds the blood vessels and nerves that keep the tooth alive", "anchor": "center"},
        {"name": "Root", "note": "fixes the tooth firmly into the socket in the jaw", "anchor": "bottom"},
    ],
    "human skeleton": [
        {"name": "Skull", "note": "bony case that protects the brain", "anchor": "top"},
        {"name": "Rib cage", "note": "protects the heart and lungs and helps in breathing", "anchor": "center"},
        {"name": "Vertebral column", "note": "supports the body and shields the spinal cord", "anchor": "back"},
        {"name": "Pelvic girdle", "note": "joins the legs to the backbone and bears the body's weight", "anchor": "bottom"},
    ],
    "human liver": [
        {"name": "Right lobe", "note": "the larger lobe; most bile is made here", "anchor": "right"},
        {"name": "Left lobe", "note": "smaller lobe lying across the stomach", "anchor": "left"},
        {"name": "Gall bladder", "note": "stores the bile until it is needed for digesting fat", "anchor": "bottom", "color": "#3E8E5A"},
    ],
    "human stomach": [
        {"name": "Cardiac sphincter", "note": "ring of muscle letting food in and stopping it flowing back", "anchor": "top"},
        {"name": "Gastric glands", "note": "release hydrochloric acid, mucus and the enzyme pepsin", "anchor": "center"},
        {"name": "Pyloric sphincter", "note": "controls how fast food passes into the small intestine", "anchor": "bottom-right"},
    ],
    "mitochondria": [
        {"name": "Outer membrane", "note": "smooth boundary separating the organelle from the cytoplasm", "anchor": "top"},
        {"name": "Inner membrane", "note": "folded inward to give a much larger surface for respiration", "anchor": "center"},
        {"name": "Cristae", "note": "the folds themselves, where most ATP is produced", "anchor": "left"},
        {"name": "Matrix", "note": "fluid inside holding enzymes, ribosomes and its own DNA", "anchor": "center"},
    ],
    "chloroplast": [
        {"name": "Thylakoid", "note": "flattened sac holding the chlorophyll that traps sunlight", "anchor": "center", "color": "#3E8E5A"},
        {"name": "Granum", "note": "stack of thylakoids where the light reactions happen", "anchor": "left", "color": "#3E8E5A"},
        {"name": "Stroma", "note": "fluid where carbon dioxide is fixed into sugar", "anchor": "right"},
        {"name": "Double membrane", "note": "two membranes enclosing the whole organelle", "anchor": "top"},
    ],
    "spinal cord": [
        {"name": "Grey matter", "note": "butterfly-shaped core of nerve cell bodies", "anchor": "center"},
        {"name": "White matter", "note": "outer nerve fibres carrying impulses to and from the brain", "anchor": "left"},
        {"name": "Spinal nerve", "note": "branches out to carry impulses to the rest of the body", "anchor": "right"},
        {"name": "Vertebra", "note": "bone of the backbone that encloses and protects the cord", "anchor": "back"},
    ],
    "rib cage": [
        {"name": "True ribs", "note": "the upper seven pairs joined directly to the breastbone", "anchor": "top"},
        {"name": "False ribs", "note": "joined to the breastbone through the cartilage of the rib above", "anchor": "center"},
        {"name": "Floating ribs", "note": "the last two pairs, attached only to the backbone", "anchor": "bottom"},
        {"name": "Sternum", "note": "flat breastbone at the front that the ribs attach to", "anchor": "front"},
    ],
    "amoeba": [
        {"name": "Pseudopodia", "note": "false feet of flowing cytoplasm used to move and trap food", "anchor": "left"},
        {"name": "Nucleus", "note": "controls the activities of this single-celled organism", "anchor": "center"},
        {"name": "Food vacuole", "note": "bubble where engulfed food is digested", "anchor": "right"},
        {"name": "Contractile vacuole", "note": "pumps out excess water that seeps in", "anchor": "bottom"},
    ],

    "human skull": [
        {"name": "Cranium", "note": "domed box of fused bones that protects the brain", "anchor": "top"},
        {"name": "Mandible", "note": "lower jaw, the only bone of the skull that can move", "anchor": "bottom"},
        {"name": "Maxilla", "note": "upper jaw bone holding the top row of teeth", "anchor": "front"},
        {"name": "Orbit", "note": "bony socket that holds and protects the eyeball", "anchor": "front-top"},
    ],
    "hand bones": [
        {"name": "Carpals", "note": "eight small wrist bones that let the hand bend and twist", "anchor": "bottom"},
        {"name": "Metacarpals", "note": "five long bones forming the palm", "anchor": "center"},
        {"name": "Phalanges", "note": "finger bones; three in each finger and two in the thumb", "anchor": "top"},
    ],
    "paramecium": [
        {"name": "Cilia", "note": "tiny hair-like threads that beat together to swim and sweep in food", "anchor": "left"},
        {"name": "Oral groove", "note": "channel that funnels food particles into the cell", "anchor": "front"},
        {"name": "Macronucleus", "note": "large nucleus controlling the everyday working of the cell", "anchor": "center"},
        {"name": "Contractile vacuole", "note": "pumps out the excess water that constantly seeps in", "anchor": "right"},
    ],
    "solar panel": [
        {"name": "Photovoltaic cell", "note": "silicon cell that turns sunlight straight into electric current", "anchor": "front", "color": "#3B7DD8"},
        {"name": "Glass cover", "note": "protects the cells while letting sunlight through", "anchor": "top"},
        {"name": "Frame", "note": "holds the panel rigid and lets it be tilted towards the Sun", "anchor": "left"},
    ],

    # ── Geography and earth science ───────────────────────────────────────
    "glacier": [
        {"name": "Accumulation zone", "note": "upper region where more snow falls than melts", "anchor": "top"},
        {"name": "Crevasse", "note": "deep crack opening where the ice is stretched", "anchor": "center"},
        {"name": "Moraine", "note": "rock and soil carried along and dumped by the ice", "anchor": "left"},
        {"name": "Snout", "note": "melting front end where the meltwater stream begins", "anchor": "bottom"},
    ],
    "river": [
        {"name": "Meander", "note": "wide loop the river swings through across flat land", "anchor": "center"},
        {"name": "Outer bank", "note": "water flows fastest here, so it is worn away by erosion", "anchor": "right"},
        {"name": "Inner bank", "note": "water slows here and drops its sediment, building a beach", "anchor": "left"},
        {"name": "Flood plain", "note": "flat fertile land built from silt left by past floods", "anchor": "bottom"},
    ],
    "dam": [
        {"name": "Reservoir", "note": "artificial lake storing water behind the wall", "anchor": "back", "color": "#3B7DD8"},
        {"name": "Spillway", "note": "channel letting surplus water escape safely during floods", "anchor": "right"},
        {"name": "Penstock", "note": "pipe carrying water down to the turbines under pressure", "anchor": "center"},
        {"name": "Powerhouse", "note": "houses the turbines and generators that make electricity", "anchor": "bottom-front"},
    ],
    "mountain": [
        {"name": "Peak", "note": "highest point of the mountain", "anchor": "top"},
        {"name": "Snow line", "note": "height above which snow stays all year round", "anchor": "top-front"},
        {"name": "Slope", "note": "steep sides shaped by weathering and running water", "anchor": "left"},
        {"name": "Valley", "note": "low ground between two ranges, often carved by a river", "anchor": "bottom"},
    ],
    "solar system": [
        {"name": "Sun", "note": "star at the centre; its gravity holds every planet in orbit", "anchor": "center", "color": "#B8791F"},
        {"name": "Inner planets", "note": "Mercury, Venus, Earth and Mars — small, rocky and close in", "anchor": "left"},
        {"name": "Outer planets", "note": "Jupiter, Saturn, Uranus and Neptune — large gas and ice giants", "anchor": "right"},
        {"name": "Orbit", "note": "elliptical path each planet follows around the Sun", "anchor": "top"},
    ],
    "moon": [
        {"name": "Crater", "note": "bowl-shaped pit blasted out by a meteorite impact", "anchor": "front"},
        {"name": "Maria", "note": "dark flat plains of ancient hardened lava", "anchor": "left"},
        {"name": "Highlands", "note": "pale, heavily cratered and older regions", "anchor": "top"},
    ],

    # Anchors are in the viewer's frame, and this scan faces the camera (an
    # anterior view: vena cava on the viewer's left, pulmonary trunk sweeping
    # right). So the heart's RIGHT side sits on the viewer's LEFT, exactly as in
    # the NCERT figure. These were once mirrored, putting "Right atrium" on the
    # left atrium.
    "human heart": [
        {"name": "Aorta", "note": "largest artery, carries blood to the body", "anchor": "top", "color": "#D64545"},
        {"name": "Pulmonary artery", "note": "carries blood to the lungs", "anchor": "top-right", "color": "#3B7DD8"},
        {"name": "Right atrium", "note": "receives deoxygenated blood from the body", "anchor": "left", "color": "#3B7DD8"},
        {"name": "Left atrium", "note": "receives oxygenated blood from the lungs", "anchor": "right", "color": "#D64545"},
        {"name": "Right ventricle", "note": "pumps blood to the lungs", "anchor": "left-bottom", "color": "#3B7DD8"},
        {"name": "Left ventricle", "note": "pumps blood to the whole body", "anchor": "right-bottom", "color": "#D64545"},
    ],
    "human brain": [
        {"name": "Cerebrum", "note": "thinking, memory and voluntary action", "anchor": "top-front"},
        {"name": "Cerebellum", "note": "balance and muscular coordination", "anchor": "back-bottom"},
        {"name": "Medulla oblongata", "note": "controls breathing and heartbeat", "anchor": "bottom"},
        {"name": "Frontal lobe", "note": "reasoning and decision making", "anchor": "front"},
    ],
    "human eye": [
        {"name": "Cornea", "note": "transparent front, bends entering light", "anchor": "front"},
        {"name": "Iris", "note": "coloured ring, controls pupil size", "anchor": "front-top"},
        {"name": "Lens", "note": "focuses light sharply on the retina", "anchor": "center"},
        {"name": "Retina", "note": "light-sensitive screen at the back", "anchor": "back"},
        {"name": "Optic nerve", "note": "carries impulses to the brain", "anchor": "back-bottom"},
    ],
    "neuron": [
        {"name": "Dendrite", "note": "receives impulses from other neurons", "anchor": "left"},
        {"name": "Cell body", "note": "contains the nucleus and cytoplasm", "anchor": "left-top"},
        {"name": "Axon", "note": "carries the impulse away from the cell body", "anchor": "center"},
        {"name": "Myelin sheath", "note": "insulates and speeds up the impulse", "anchor": "right-top"},
        {"name": "Axon terminal", "note": "passes the impulse to the next cell", "anchor": "right"},
    ],
    "animal cell": [
        {"name": "Nucleus", "note": "controls all activities of the cell", "anchor": "center"},
        {"name": "Cell membrane", "note": "selectively permeable outer boundary", "anchor": "top"},
        {"name": "Cytoplasm", "note": "jelly-like fluid holding the organelles", "anchor": "left"},
        {"name": "Mitochondria", "note": "powerhouse, releases energy as ATP", "anchor": "bottom-right"},
    ],
    "plant cell": [
        {"name": "Cell wall", "note": "rigid outer covering of cellulose", "anchor": "top"},
        {"name": "Chloroplast", "note": "green plastid, site of photosynthesis", "anchor": "left", "color": "#3E8E5A"},
        {"name": "Vacuole", "note": "large sac storing cell sap", "anchor": "center"},
        {"name": "Nucleus", "note": "controls all activities of the cell", "anchor": "top-right"},
    ],
    "human lungs": [
        {"name": "Trachea", "note": "windpipe carrying air downward", "anchor": "top"},
        {"name": "Bronchi", "note": "two tubes entering the lungs", "anchor": "top-center"},
        {"name": "Bronchioles", "note": "fine branching air tubes", "anchor": "center"},
        {"name": "Alveoli", "note": "air sacs where gases are exchanged", "anchor": "bottom"},
    ],
    "human kidney": [
        {"name": "Cortex", "note": "outer region containing the nephrons", "anchor": "top"},
        {"name": "Medulla", "note": "inner region with the pyramids", "anchor": "center"},
        {"name": "Renal artery", "note": "brings blood in to be filtered", "anchor": "left-top", "color": "#D64545"},
        {"name": "Ureter", "note": "carries urine to the urinary bladder", "anchor": "bottom-left"},
    ],
    "nephron": [
        {"name": "Bowman's capsule", "note": "cup that collects the filtrate", "anchor": "top-left"},
        {"name": "Glomerulus", "note": "ball of capillaries that filters blood", "anchor": "top"},
        {"name": "Loop of Henle", "note": "reabsorbs water and salts", "anchor": "bottom"},
        {"name": "Collecting duct", "note": "carries urine towards the ureter", "anchor": "right"},
    ],
    "digestive system": [
        {"name": "Oesophagus", "note": "food pipe leading to the stomach", "anchor": "top"},
        {"name": "Stomach", "note": "churns food with acid and enzymes", "anchor": "left-top"},
        {"name": "Liver", "note": "secretes bile that emulsifies fat", "anchor": "right-top"},
        {"name": "Small intestine", "note": "absorbs the digested food", "anchor": "center"},
        {"name": "Large intestine", "note": "absorbs water from the waste", "anchor": "bottom"},
    ],
    "volcano": [
        {"name": "Ash cloud", "note": "gas, steam and rock dust blasted upward", "anchor": "top"},
        {"name": "Crater", "note": "opening at the summit of the cone", "anchor": "top-front"},
        {"name": "Main vent", "note": "pipe the magma rises through", "anchor": "center", "color": "#C2410C"},
        {"name": "Magma chamber", "note": "molten rock stored deep underground", "anchor": "bottom", "color": "#D64545"},
        {"name": "Lava flow", "note": "magma once it reaches the surface", "anchor": "right-bottom", "color": "#C2410C"},
    ],
    "earth": [
        {"name": "Crust", "note": "thin solid outer layer we live on", "anchor": "top"},
        {"name": "Mantle", "note": "thick layer of slow-flowing hot rock", "anchor": "left"},
        {"name": "Outer core", "note": "liquid iron and nickel", "anchor": "right"},
        {"name": "Inner core", "note": "solid iron centre, hottest part", "anchor": "center"},
    ],
    "atom": [
        {"name": "Nucleus", "note": "central core of protons and neutrons", "anchor": "center"},
        {"name": "Proton", "note": "positively charged particle", "anchor": "center"},
        {"name": "Neutron", "note": "particle with no charge", "anchor": "center"},
        {"name": "Electron", "note": "negative particle in a shell around it", "anchor": "top"},
    ],
    "dna": [
        {"name": "Sugar-phosphate backbone", "note": "the two twisted outer strands", "anchor": "left"},
        {"name": "Nitrogenous bases", "note": "A, T, G and C forming the rungs", "anchor": "center"},
        {"name": "Hydrogen bond", "note": "holds the paired bases together", "anchor": "right"},
        {"name": "Double helix", "note": "the full twisted-ladder shape", "anchor": "top"},
    ],
}


def normalize_topic(text: str) -> str:
    """Lowercase, de-noise and alias a topic to a curated key when possible."""
    t = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    t = re.sub(
        r"\b(3d|model|models|diagram|structure of the|structure of|show me( the)?|"
        r"the|a|an|of|please|labelled|labeled|explain|draw)\b",
        " ",
        t,
    )
    t = re.sub(r"\s+", " ", t).strip()
    if t in CURATED_MODELS:
        return t
    if t in _ALIASES:
        return _ALIASES[t]
    # "structure of the human heart in detail" still has to find "human heart".
    for key in CURATED_MODELS:
        if key in t:
            return key
    for alias, key in _ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", t):
            return key
    return t


# ── Label anchors -> normalized offsets ───────────────────────────────────

_AXES = {
    "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0),
    "top": (0.0, 1.0, 0.0), "bottom": (0.0, -1.0, 0.0),
    "front": (0.0, 0.0, 1.0), "back": (0.0, 0.0, -1.0),
    "center": (0.0, 0.0, 0.0), "centre": (0.0, 0.0, 0.0),
}

# Distinct hues for parts that carry no conventional colour of their own. Only
# ever applied inside the 3D viewport — the surrounding UI stays monochrome.
_LABEL_PALETTE = [
    "#D64545", "#3B7DD8", "#3E8E5A", "#B8791F",
    "#8B5CF6", "#0E9298", "#C2410C", "#4B5563",
]


def normalize_labels(raw_labels, topic_key: str = "") -> List[dict]:
    """Turn semantic anchors into -1..1 offsets and give every part a colour.

    A model has never seen the mesh it is labelling, so it emits a word from a
    closed vocabulary ("back-bottom") rather than coordinates. The browser
    resolves these against the loaded model's real bounding box, which is the
    only place the true dimensions are known.
    """
    source = CURATED_LABELS.get(topic_key) or raw_labels or []

    labels: List[dict] = []
    for i, item in enumerate(list(source)[:6]):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue

        parts = [
            p for p in re.split(r"[-_\s]+", str(item.get("anchor") or "").lower())
            if p in _AXES
        ]
        if parts:
            x = sum(_AXES[p][0] for p in parts) / len(parts)
            y = sum(_AXES[p][1] for p in parts) / len(parts)
            z = sum(_AXES[p][2] for p in parts) / len(parts)
        else:
            x = y = z = 0.0

        labels.append({
            "name": name[:44],
            "note": str(item.get("note") or "").strip()[:130],
            "offset": [round(x, 3), round(y, 3), round(z, 3)],
            "color": item.get("color") or _LABEL_PALETTE[i % len(_LABEL_PALETTE)],
        })
    return labels


# ══════════════════════════════════════════════════════════════════════════
#  Sketchfab
# ══════════════════════════════════════════════════════════════════════════

_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "in", "on", "with", "to",
    "3d", "model", "models", "structure", "diagram", "show", "me", "human",
}

_SCIENCE_TAGS = {
    "science", "scientific", "physics", "chemistry", "biology", "education",
    "educational", "school", "anatomy", "anatomical", "medical", "molecule",
    "molecular", "atom", "atomic", "element", "chemical", "proton", "neutron",
    "electron", "particles", "cell", "organ", "skeleton", "bone", "dna",
    "astronomy", "planet", "space", "geology", "geography", "mathematics",
    "geometry", "engineering", "microscope", "laboratory", "lab", "medicine",
    "biological", "organism", "botany", "zoology", "physiology", "histology",
}

# Marketplace content a student never asked for. "Moon Shoes" and the Real
# Steel robot both die here.
_OFF_TOPIC_TAGS = {
    "handbag", "bag", "purse", "backpack", "shoe", "shoes", "sneaker",
    "clothing", "fashion", "dress", "wedding", "furniture", "chair", "sofa",
    "table", "coffeetable", "sidetable", "consoletable", "armchair", "lamp",
    "interior", "interiordesign", "livingroom", "weapon", "gun", "rifle",
    "sword", "knife", "raygun", "car", "vehicle", "truck", "motorcycle",
    "architecture", "house", "food", "watch", "cap", "hat", "jewelry", "ring",
    "toy", "cartoon", "anime", "sailormoon", "halloween", "ghost", "game",
    "games", "game-ready", "gameready", "lowpoly", "fps", "rpg", "character",
    "rigged", "scifi", "sci-fi", "cyberpunk", "mech", "robot", "robots",
    "droid", "statue", "logo", "minecraft", "clubpenguin", "lego", "decor",
    "realsteel", "boxing", "dreamworks", "createdwithai", "meshy",
    # Franchise and game-skin noise. Free 3D search for a school topic is
    # dominated by these: "glacier" returns a PUBG rifle skin, "rocket" the
    # Guardians of the Galaxy character, "prism" a magical energy stone.
    "pubg", "fortnite", "warzone", "callofduty", "csgo", "valorant", "roblox",
    "skin", "camo", "marvel", "avengers", "guardians", "disney", "pixar",
    "magical", "magic", "wizard", "fantasy", "medieval", "cosplay", "prop",
    "collectible", "figurine", "keychain", "sticker", "emote",
}


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def score_result(result: dict, query: str) -> float:
    """Rank one Sketchfab hit against the query. Higher is better."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0

    name = (result.get("name") or "").strip()
    name_tokens = _tokenize(name)
    descriptive = {
        (t.get("name") or "").lower() for t in (result.get("tags") or []) if t.get("name")
    } | {
        (c.get("name") or "").lower() for c in (result.get("categories") or []) if c.get("name")
    }

    score = 0.0
    if name.lower() == query.strip().lower():
        score += 6.0

    name_cover = len(query_tokens & name_tokens) / len(query_tokens)
    score += 4.0 * name_cover
    score += 2.5 * len(query_tokens & descriptive) / len(query_tokens)

    # The title has to be about the thing asked for. "Moon Shoes" covers the
    # token "moon" fully, so coverage alone is not enough — a title carrying
    # extra nouns that are themselves off-topic is what gives it away.
    if descriptive & _SCIENCE_TAGS:
        score += 2.5

    unrequested_noise = (descriptive & _OFF_TOPIC_TAGS) - query_tokens
    if unrequested_noise:
        score -= 5.0
    if (name_tokens & _OFF_TOPIC_TAGS) - query_tokens:
        score -= 3.0

    # Photogrammetry scans and 200-triangle placeholders both teach nothing.
    faces = result.get("faceCount") or 0
    if faces < 1500 or faces > 400_000:
        score -= 2.0

    size = ((result.get("archives") or {}).get("glb") or {}).get("size") or 0
    if size and size > MAX_MODEL_BYTES:
        score -= 3.0

    score += min((result.get("likeCount") or 0) / 400.0, 1.0)
    return score


# Below this the best candidate is not convincingly on-topic. Showing a student
# the wrong object is worse than showing none, so we show none.
MIN_SCORE = 3.0


# ── Sketchfab transport ───────────────────────────────────────────────────
#
# urllib, not httpx, and deliberately so. Sketchfab answers every authenticated
# httpx request with "202 Accepted" and an empty body — /v3/me included, so it
# is neither a download quota nor a bad API key — while the identical request
# from urllib or curl returns 200. It fingerprints the client and httpx does
# not get through. Moving this file to httpx silently broke every model
# download and sent the whole app to the iframe-embed fallback.
#
# urllib blocks, so each call is handed to a worker thread: the event loop
# stays free, which was the reason for leaving urllib in the first place.

def _sync_get(url: str, token: Optional[str] = None, timeout: int = 25):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Token {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


async def _get_json(url: str, token: Optional[str] = None) -> Optional[dict]:
    try:
        status, body = await asyncio.to_thread(_sync_get, url, token)
        if status != 200:
            print(f"[3D] {url.split('?')[0]} returned {status}")
            return None
        return json.loads(body.decode())
    except Exception as e:
        print(f"[3D] request failed for {url.split('?')[0]}: {e}")
        return None


async def _sketchfab_search(query: str, token: Optional[str]) -> Optional[str]:
    """UID of the best on-topic downloadable model, or None."""
    url = (
        "https://api.sketchfab.com/v3/search?type=models"
        "&downloadable=true&count=24&sort_by=-likeCount"
        f"&q={quote(query)}"
    )
    data = await _get_json(url, token)
    results = (data or {}).get("results") or []

    ranked = sorted(
        ((score_result(r, query), r) for r in results), key=lambda p: p[0], reverse=True
    )
    if not ranked:
        return None

    best_score, best = ranked[0]
    print(
        f"[3D SEARCH] query={query!r} best={best.get('name')!r} "
        f"score={best_score:.2f} (of {len(results)})"
    )
    if best_score < MIN_SCORE:
        print("[3D SEARCH] nothing cleared the relevance bar")
        return None
    return best.get("uid")


async def _sketchfab_glb_url(uid: str, token: str):
    """Signed GLB URL and its size. The URL is valid for only 300 seconds."""
    data = await _get_json(f"https://api.sketchfab.com/v3/models/{uid}/download", token)
    glb = (data or {}).get("glb") or {}
    url = glb.get("url")
    return (url, int(glb.get("size") or 0)) if url else None


# ══════════════════════════════════════════════════════════════════════════
#  Streaming, not caching
#
#  Nothing is written to disk. A resolved model is handed to the browser as
#  /api/v1/models/<key>.glb, and that endpoint fetches the mesh from upstream
#  and streams it straight through, so the bytes exist only for the length of
#  the request and are gone once the student has seen it.
#
#  The stable key is what makes this work despite Sketchfab's signed URLs
#  expiring after 300 seconds: the artifact persisted in the student's browser
#  points at our key, never at a signed URL, so reopening an old chat simply
#  re-resolves it. The only thing kept in memory is the signed URL itself, for
#  slightly less than its lifetime, so that a reload inside the same lesson
#  does not ask Sketchfab for a second one.
# ══════════════════════════════════════════════════════════════════════════

_SIGNED_URL_TTL = 240.0
_signed_urls: Dict[str, Tuple[str, float]] = {}


def public_url_for(key: str) -> str:
    return f"/api/v1/models/{key}.glb"


def _remember_signed_url(key: str, url: str) -> None:
    _signed_urls[key] = (url, time.monotonic() + _SIGNED_URL_TTL)


def _recall_signed_url(key: str) -> Optional[str]:
    found = _signed_urls.get(key)
    if not found:
        return None
    url, expires = found
    if time.monotonic() >= expires:
        _signed_urls.pop(key, None)
        return None
    return url


async def source_url_for(key: str) -> Optional[str]:
    """A currently-valid upstream URL for a model key, re-resolving if stale."""
    cached = _recall_signed_url(key)
    if cached:
        return cached

    if key.startswith("sk_"):
        token = os.getenv("SKETCHFAB_API_KEY")
        if not token:
            return None
        found = await _sketchfab_glb_url(key[3:], token)
        if not found:
            return None
        url, _size = found
        _remember_signed_url(key, url)
        return url

    # Generated models have no re-resolvable source: the task id is gone once
    # the generation expires, so all we can do is reuse the URL while it lasts.
    return None


def stream_model(url: str, max_bytes: int = MAX_MODEL_BYTES):
    """Yield the mesh in chunks. Nothing touches the filesystem."""
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=180) as response:
        declared = int(response.headers.get("content-length") or 0)
        if declared and declared > max_bytes:
            raise ValueError(f"model is {declared / 1e6:.1f}MB, over the cap")
        sent = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            sent += len(chunk)
            if sent > max_bytes:
                raise ValueError("model exceeded the size cap mid-stream")
            yield chunk


# ══════════════════════════════════════════════════════════════════════════
#  Tripo3D — last resort only
# ══════════════════════════════════════════════════════════════════════════

async def _tripo_generate(client: httpx.AsyncClient, query: str, api_key: str) -> Optional[str]:
    """Text-to-3D. Returns a signed GLB URL, or None.

    Deliberately the LAST option. Generated meshes are untextured and
    anatomically invented, which is fine for "a regular dodecahedron" and
    actively misleading for "a human heart".
    """
    try:
        res = await client.post(
            "https://api.tripo3d.ai/v2/openapi/task",
            json={"type": "text_to_model", "prompt": query},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        res.raise_for_status()
        task_id = res.json()["data"]["task_id"]
    except Exception as e:
        print(f"[3D] Tripo3D submit failed: {e}")
        return None

    for _ in range(15):
        await asyncio.sleep(2)  # asyncio, not time.sleep — the worker stays free
        try:
            poll = await client.get(
                f"https://api.tripo3d.ai/v2/openapi/task/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            poll.raise_for_status()
            data = poll.json()["data"]
            status = data.get("status")
            if status == "success":
                return (data.get("result") or {}).get("model", {}).get("url")
            if status in ("failed", "cancelled", "banned", "expired"):
                print(f"[3D] Tripo3D task {status}")
                return None
        except Exception as e:
            print(f"[3D] Tripo3D poll failed: {e}")
            return None
    print("[3D] Tripo3D timed out")
    return None


# ══════════════════════════════════════════════════════════════════════════
#  PubChem
# ══════════════════════════════════════════════════════════════════════════

async def pubchem_lookup(client: httpx.AsyncClient, name: str) -> Optional[dict]:
    """CID for a chemical name plus whether PubChem holds a real 3D conformer.

    PubChem silently serves the flat 2D depiction when no 3D conformer exists,
    so a "3D molecule" could render as a flat diagram with no warning. The
    3D record is probed here and the answer travels with the artifact.
    """
    encoded = quote(name.strip(), safe="")
    try:
        res = await client.get(
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{encoded}/cids/JSON"
        )
        res.raise_for_status()
        cids = (res.json().get("IdentifierList") or {}).get("CID") or []
        if not cids:
            return None
        cid = str(cids[0])
    except Exception as e:
        print(f"[3D] PubChem lookup failed for {name!r}: {e}")
        return None

    has_3d = False
    try:
        probe = await client.get(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/SDF",
            params={"record_type": "3d"},
        )
        has_3d = probe.status_code == 200 and b"V2000" in probe.content[:4000]
    except Exception:
        pass

    return {"cid": cid, "has_3d": has_3d}


# ══════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════

async def resolve_widget(wtype: str, query: str) -> Optional[dict]:
    """Resolve a 3D intent into a renderable payload.

    Order: curated pin -> Sketchfab search -> Tripo3D generation. The old code
    ran that backwards and showed generated blobs in place of real models.
    """
    topic_key = normalize_topic(query)
    cache_key = f"{wtype}:{topic_key}"
    if cache_key in _RESOLVED:
        print(f"[3D] cache hit for {topic_key!r}")
        return dict(_RESOLVED[cache_key])

    sk_token = os.getenv("SKETCHFAB_API_KEY")
    tripo_key = os.getenv("TRIPO_API_KEY")
    result: Optional[dict] = None

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        if wtype == "molecule":
            found = await pubchem_lookup(client, query)
            if found:
                result = {
                    "type": "molecule", "source": "cid",
                    "id": found["cid"], "has3d": found["has_3d"],
                }
            else:
                print(f"[3D] {query!r} is not a PubChem compound; trying object search")
                wtype = "search"

        if result is None and wtype in ("search", "generate"):
            uid = CURATED_MODELS.get(topic_key)
            if uid:
                print(f"[3D] curated model for {topic_key!r}")
            elif wtype == "search":
                uid = await _sketchfab_search(query, sk_token)

            if uid and sk_token:
                # Confirm upstream really has a GLB before promising one, and
                # keep the signed URL just long enough for the student's first
                # view. The artifact itself only ever carries our own key.
                found = await _sketchfab_glb_url(uid, sk_token)
                if found:
                    url, _size = found
                    key = f"sk_{uid}"
                    _remember_signed_url(key, url)
                    result = {
                        "type": "gltf",
                        "url": public_url_for(key),
                        "credit": "Sketchfab",
                    }

            # The embed needs no download at all, so it still covers the case
            # where the model exists but we could not get a GLB for it.
            if result is None and uid:
                print(f"[3D] falling back to the Sketchfab embed for {topic_key!r}")
                result = {"type": "sketchfab", "id": uid}

            # Generation is the fallback, never the first choice.
            if result is None and tripo_key:
                print(f"[3D] falling back to Tripo3D generation for {query!r}")
                glb = await _tripo_generate(client, query, tripo_key)
                if glb:
                    key = "tp_" + hashlib.sha1(topic_key.encode()).hexdigest()[:16]
                    _remember_signed_url(key, glb)
                    result = {
                        "type": "gltf", "url": public_url_for(key),
                        "credit": "AI-generated", "generated": True,
                    }

    if result:
        result["topic"] = topic_key
        _RESOLVED[cache_key] = dict(result)
    else:
        print(f"[3D] no model resolved for {query!r}")
    return result
