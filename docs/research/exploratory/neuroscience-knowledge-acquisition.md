## Overview

This dossier traces the neuroscience literature on knowledge acquisition — how synapses strengthen during learning and how memories are retrieved "on demand" — across a roughly 75-year arc: from Donald Hebb's 1949 theoretical postulate through the discovery of long-term potentiation (LTP), the molecular biology of memory consolidation in Aplysia, synaptic tagging and capture, dendritic spine imaging of skill learning, memory engram labeling and optogenetics, systems consolidation during sleep, and 2024–2026 work on engram dynamics, astrocytes, and engram reprogramming. For each landmark paper, the dossier lists the crucial references it built on or spawned, and digests a second round of research on those references, producing a layered citation genealogy rather than a flat list.

## 1949 — Hebb's Theoretical Foundation

Donald Hebb's *The Organization of Behavior* (1949) proposed that repeated, correlated firing between two connected neurons produces a "growth process or metabolic change" that increases the efficiency of the presynaptic cell in driving the postsynaptic cell — the postulate now known as the Hebb synapse or "cells that fire together, wire together". Hebb further proposed "cell assemblies" (functionally interconnected neuron groups) and "phase sequences" as the substrate of thought, providing the first bridge between neurophysiology and psychological learning theory.[1][2][3][4]

**Crucial references embedded in/spawned by this paper:**
- Lorente de Nó's reverberatory circuit theory (cited by Hebb as precedent for sustained neural activity).[1]
- Historical review noting that "Hebbian" ideas partially predate Hebb, tracing similar theoretical claims to early 20th-century physiologists.[1]
- Direct algorithmic formalizations: the Hebb learning rule as expressed for neural network models (∆w = presynaptic × postsynaptic activity).[5]

**Second-round digestion:** Follow-up historiographical work (Brown, 2020/2021, in *Molecular Brain*/PMC) shows Hebb's postulate was not experimentally testable in 1949 — it took until the 1970s–1980s for a physiological correlate (LTP) to be identified, and until the 1990s–2000s for the molecular machinery (NMDA receptors, CREB, protein synthesis) to be characterized, which is the throughline the rest of this dossier follows.[6][7][1]

## 1966–1973 — Discovery of Long-Term Potentiation

Terje Lømo (1966) first observed that brief high-frequency stimulation of the perforant pathway increased synaptic efficiency in the rabbit dentate gyrus for hours. Tim Bliss joined Lømo's lab in 1968, and together they published the landmark 1973 papers in the *Journal of Physiology* demonstrating "long-lasting potentiation" of synaptic transmission — the first physiological demonstration of a Hebbian-type synaptic mechanism.[8][9][10][11]

**Crucial references:**
- Lømo, T. (1966) — unpublished preliminary observations of perforant-path potentiation.[9]
- Andersen & Lømo (1967) and Bliss & Lømo (1970) — precursor work on cortical plasticity pathways establishing recording methodology.[11]
- Bliss & Gardner-Medwin (1973) — the companion paper in chronically implanted, unanesthetized rabbits showing LTP could last days to months.[10][11]
- Scoville & Milner (1957) — the patient H.M. hippocampal-lesion study that motivated interest in the hippocampus as a memory structure.[10]

**Second-round digestion:** Patihis's historical analysis (PMC/Port.ac.uk) shows that what distinguished the 1973 paper from Bliss & Lømo's earlier drafts was a change in stimulation protocol (single strong tetanus flanked by weak probe stimuli), which allowed precise, hours-long tracking of potentiation decay — a methodological innovation, not just a new finding. Later reviews (Bliss, Collingridge, Morris & Reymann, Bristol) trace how "saturation" of LTP after repeated tetani foreshadowed the concept of metaplasticity, and identify three converging expression mechanisms (one presynaptic, two postsynaptic) established by subsequent decades of research.[8][11]

## 1990s — Molecular Biology of Memory in Aplysia (Kandel Laboratory)

Eric Kandel's group used the sea slug *Aplysia californica* to dissect the molecular switch between short- and long-term synaptic facilitation. Dash et al. (1990) first implicated the CRE/CREB pathway; Bartsch et al. (1995) showed CREB1 (activator) and CREB2 (repressor) jointly gate the transition to long-term facilitation (LTF). Martin et al. (1997) demonstrated that this facilitation is synapse-specific and requires local protein synthesis at the stimulated synapse rather than only in the soma.[12][13][14][15]

**Crucial references:**
- Dash, Hochner & Kandel (1990) — first CRE-oligonucleotide blocking experiment implicating CREB.[12]
- Bartsch et al. (1995, 1998, 1999) — CREB1/CREB2 regulatory unit and local stabilization of CREB-mediated facilitation.[16][12]
- Chen, Bailey & Kandel (1997) — synapse-specific long-term facilitation and local protein synthesis.[16]
- Bailey & Kandel (2004, 2008) — synthesis reviews on synaptic growth and long-term memory storage.[17][16]
- Rajasethupathy et al. (2009) — miR-124 constrains CREB-mediated facilitation, adding an RNA-regulatory layer.[18]

**Second-round digestion:** Pittenger & Kandel (1998) generalized the activator/repressor balance model to mice, showing CREB's role in mammalian hippocampal LTP, establishing evolutionary conservation of the transcriptional switch from mollusk to mammal. The 2015 jneurosci follow-up (Hu et al.) closed a gap in the model by showing postsynaptic cJun/CREB2 are also required for persistent LTF, indicating bidirectional (pre- and postsynaptic) transcriptional control was needed to complete Kandel's original synaptic model.[15][12]

## 1997 — Synaptic Tagging and Capture (Frey & Morris)

Frey and Morris (Nature, 1997) proposed that LTP induction sets a transient, protein-synthesis-independent "synaptic tag" at the stimulated synapse; this tag then captures plasticity-related proteins (PRPs) synthesized elsewhere in the neuron (typically triggered by a stronger, separate stimulation) to convert early-phase LTP into a stable late-phase LTP. This mechanism explained synapse-specificity of long-term plasticity despite protein synthesis occurring mainly in the soma.[19][20]

**Crucial references:**
- Frey, Schroeder & Matthies (1990) — earlier work on dopaminergic gating of LTP that anticipated the two-component (tag + PRP) model.[21]
- Frey & Morris (1998, *Trends in Neuroscience*) — extension defining tag decay kinetics (~1–3 hours).[20][21]
- Redondo & Morris (2011, *Nat Rev Neurosci*) — comprehensive review formalizing the "synaptic tagging and capture" (STC) framework and cataloguing challenges to the original model.[20]

**Second-round digestion:** Moncada & Viola (2007) and the PNAS "behavioral tagging" paper (2009) translated STC from cellular electrophysiology into whole-animal behavior, showing that a weak learning event paired temporally with a novel, salient experience can become a persistent long-term memory — directly linking the synaptic mechanism to real-world "on-demand" memory strengthening through incidental co-occurring salience. A 2024 review (PMC, "Synapses tagged, memories kept") reaffirms STC as still-active framework 27 years later, cataloguing molecular identities of PRPs (proteins and mRNAs) now confirmed.[22][23][24][25]

## 2000 — Memory Reconsolidation (Nader, Schafe & LeDoux)

Nader et al. (Nature, 2000) showed that reactivating (retrieving) a consolidated fear memory returns it to a labile, protein-synthesis-dependent state; blocking protein synthesis (anisomycin) immediately after retrieval erases the memory, whereas blocking it without retrieval leaves memory intact. This overturned the assumption that consolidation is a one-time, irreversible event and reframed memory as something rebuilt every time it is recalled "on demand".[26][27][28]

**Crucial references:**
- Misanin, Miller & Lewis (1968) — earlier, largely overlooked demonstration of amnesic susceptibility of reactivated memories, cited by Nader as precedent.[27][28]
- Nader & LeDoux (2002, *Neuron*) — extension showing reconsolidation also occurs in the hippocampus for contextual (not just amygdalar cued) fear memories.[26]
- Nader, Schafe & LeDoux (2000, *Nat Rev Neurosci*) — theoretical reframing of consolidation as an iterative rather than singular process.[27]

**Second-round digestion:** A 2022 review (PubMed 36202323) traces how reconsolidation research bifurcated into two applied streams: (1) using reconsolidation-interference to weaken traumatic memories for PTSD treatment, and (2) using "tagging along" retrieval-based reconsolidation to strengthen everyday and spatial memories — showing that the on-demand recall mechanism described by Nader et al. can be therapeutically exploited in both directions.[29][30]

## 2009 — Structural Plasticity of Skill Learning (Dendritic Spine Imaging)

Using in vivo two-photon microscopy, Yang, Pan & Gan (Nature, 2009) showed that motor skill training (rotarod) in mice rapidly induces new dendritic spines in motor cortex; while most new spines are eliminated within weeks, a small stable fraction persists for the animal's lifetime, providing the first direct structural evidence that skill learning leaves lasting, if sparse, physical traces on cortical synapses. A companion paper by Xu et al. (2009) in sensory/reaching tasks showed spine formation begins within an hour of training onset.[31][32][33][34][35]

**Crucial references:**
- Xu, Yu, Perlik et al. (2009) — "Rapid formation and selective stabilization of synapses for enduring motor memories," the companion Nature paper published alongside Yang et al..[33][34][36]
- Trachtenberg et al. (2002) and earlier two-photon imaging methodology papers establishing longitudinal spine tracking in living cortex.[32]

**Second-round digestion:** Subsequent work fractionated this finding into causal versus correlational claims. Peters et al. (2017, PMC 5995668) directly tested whether preferential stabilization of new spines is *necessary* for skill retention and found performance gains persisted even when new-spine stabilization was disrupted — meaning spine formation predicts, but is not strictly required for, retained motor memory. Albarran et al. (2021) identified PirB (an immune receptor) as a molecular brake on spine stability, and showed that blocking PirB increases spine survival and directly enhances motor learning, turning the correlational Yang/Gan finding into a causal, pharmacologically testable mechanism. A 2021 biorxiv study further showed this spine stabilization is selective for genetically tagged ("TRAPed") engram neurons rather than uniform across the cortical circuit, tightening the link between structural plasticity and engram theory.[37][38][39][36][33]

## 2012–2015 — Memory Engrams and Optogenetic Recall

Liu, Ramirez et al. (Tonegawa lab, Science 2012) used optogenetics to label and then artificially reactivate a sparse population of hippocampal dentate gyrus neurons active during fear learning, showing that light-triggered reactivation of this "engram" alone was sufficient to produce fear behavior — the first causal demonstration that a specific neuron population, not the whole hippocampus, constitutes a memory trace. Ryan et al. (2015, *Science*) extended this to amnesic mice, showing "lost" memories after protein-synthesis blockade could still be optogenetically recalled, indicating amnesia reflects an access/retrieval deficit ("silent engram") rather than trace destruction.[40][41]

**Crucial references:**
- Josselyn & Tonegawa (2020, *Science* review, "Memory engrams: Recalling the past and imagining the future") — synthesizes excitability-based competition for engram allocation, false-memory implantation, and silent-engram discovery.[42]
- Ramirez et al. (2013) — false memory implantation via engram reactivation paired with mismatched shock.[41][43]
- Han et al. (2022) — necessity/sufficiency criteria for engram cells in recent memory retrieval.[43]

**Second-round digestion:** The engram framework matured through the 2020s into questioning its own "stability" assumption. Zaki & Cai (2024/2025, *Neuropsychopharmacology*) reviewed evidence for representational drift, memory-linking, and schema learning that make engrams far more dynamic than the original 2012 optogenetic snapshot implied. Uytiepo et al. (Maximov lab, *Science* 2025) used 3D electron microscopy to show engram neurons expand connectivity via multi-synaptic boutons rather than simply strengthening isolated synapses, and interact more with astrocytes — directly extending, and partly revising, the Hebbian assumption that engram cells preferentially wire to each other. Choucry, Nomoto & Inokuchi (2024, *Nat Rev Neurosci*) formalized "engram overlap" as the mechanism for linking temporally close memories, building a bridge between synaptic tagging (1997) and engram theory (2012).[44][45][46]

## 2019–2023 — Systems Consolidation During Sleep

Klinzing, Niethard & Born (2019, *Nat Neurosci*) and Rasch/Born and colleagues' 2023 *Neuron* review established that hippocampal "replay" of neuronal firing patterns during slow-wave sleep drives systems consolidation, gradually transferring episodic traces into gist-like neocortical schemas, with REM sleep balancing local synaptic rescaling against global homeostatic renormalization.[47][48]

**Crucial references:**
- Ji & Wilson (2007, *Nat Neurosci*) — first demonstration that hippocampal and visual-cortical firing patterns during sleep replay coordinated awake experience, establishing the cortical-hippocampal "dialogue" model.[49]
- Wilson & McNaughton (1994) — earliest hippocampal ensemble replay observation during sleep (precursor cited across this literature).
- Tononi & Cirelli's synaptic homeostasis hypothesis — cited as the counterpart mechanism (global downscaling) to local replay-driven strengthening.[47]

**Second-round digestion:** A 2025 *Nature* paper (Sekeres/DeNardo-adjacent, "Systems consolidation reorganizes hippocampal engram...") used engram-labeling tools directly inside the hippocampus to show that time-dependent loss of memory precision is driven by neurogenesis-dependent rewiring of engram circuitry within the hippocampus itself — a finding that requires revising classical systems-consolidation models (which located reorganization purely in hippocampus-to-cortex transfer) to include intra-hippocampal reorganization.[50]

## 2020–2024 — Retrieval Practice and the Testing Effect

Behavioral and fMRI studies (Rowland review era, plus Peteranderl/Karpicke-style paradigms) demonstrated that actively retrieving information ("testing") produces stronger, more durable memory than passive restudy — the "testing effect" — and that overt, enacted retrieval strengthens retention further than covert recall. A 2020 fMRI study (PubMed 33613206) found retrieval practice during training establishes a distinct striatal–supramarginal gyrus network active at final recall, differing qualitatively from the frontal-cortex-dominant activation pattern seen after passive restudy.[51][52]

**Crucial references:**
- Roediger & Karpicke (2006) — foundational behavioral demonstration of the testing effect (widely cited precursor across all these studies).
- Karpicke & Blunt (2011) — retrieval practice versus elaborative studying comparisons.
- Bjork's "new theory of disuse" — retrieval strength vs. storage strength distinction, cited as the theoretical scaffold explaining why retrieval practice outperforms restudy despite feeling harder.[53]

**Second-round digestion:** A 2026 synthesis (mysimulator.uk, drawing on Bjork, Frey & Morris 1997, and Bliss & Lømo 1973) explicitly connects the behavioral testing effect to the cellular STC mechanism: successful retrieval is proposed to function like a second "tagging" event that reactivates and re-stabilizes hippocampal traces, directly linking 1990s cellular synaptic-tagging work to observed 2020s classroom-level learning phenomena, and explaining why *spaced* retrieval (rather than massed) preferentially drives late-phase, protein-synthesis-dependent LTP.[53]

## 2024–2026 — Frontier: Astroengrams, Engram Reprogramming, and Ensemble Deconstruction

The most recent literature (through Q1 2026) expands the engram concept beyond neurons and toward reversibility of age-related decline. Berdugo-Vega et al. (2026, *Neuron*) showed that "partial reprogramming" (transient OSK transcription factor expression) of aged or Alzheimer's-model engram neurons restores memory performance to youthful levels, and that targeting hippocampal versus prefrontal engrams differentially rescues recent versus remote memories. Ortiz-Ramírez/Robles-Pacheco-adjacent work on GluA2-AMPAR trafficking (Neuron, 2026) identified a specific molecular switch that silences and "un-silences" engrams, offering a mechanistic account of why some memories become temporarily inaccessible without being erased. Pouget et al. (2026, *Nat Neurosci*) used calcium-based, temporally precise tagging to show that non-overlapping CA1 ensembles are recruited at distinct phases of a single learning episode, meaning a single "engram" is compositionally heterogeneous rather than monolithic. Meanwhile, a 2026 *Nat Rev Neurosci* piece formalized "astroengrams," presenting evidence that sparse astrocyte ensembles — not just neurons — activate during learning and that their reactivation alone can drive recall, a genuinely paradigm-shifting expansion of what counts as the cellular substrate of memory.[54][55][56][57][58]

**Crucial references within these 2025–2026 papers:**
- Uytiepo et al. (2025, *Science*) — synaptic architecture (multi-synaptic boutons, astrocyte interactions) cited as structural precedent for astroengram hypothesis.[56][45]
- Ryan, Roy, Tonegawa (2015–2021 body of work) — cited across all 2025–2026 papers as the methodological foundation (optogenetic tagging, TRAP2/Fos-based labeling) enabling this newer generation of experiments.[59][43]
- Williamson et al. (2024) — glial (non-neuronal) contribution to engram formation, the direct precursor of the 2026 "astroengram" reframing.[59]

**Second-round digestion:** These 2025–2026 papers converge on three revisions to the classical (1949–2015) model: (1) memory storage is not confined to neurons (astrocytes participate); (2) a single engram is not homogeneous but assembled from temporally distinct sub-ensembles recruited moment-by-moment during learning; and (3) engram "silencing" with age or disease is often a retrievability problem (AMPAR trafficking, chromatin/aging state) rather than trace destruction, meaning on-demand recall failure and true forgetting are mechanistically distinguishable and, per the reprogramming studies, potentially reversible.[57][54][56][44]

## Cross-Cutting Table: Landmark Papers and Their Reference Lineages

| Era | Landmark paper | Core finding | Key downstream reference digested |
|---|---|---|---|
| 1949 | Hebb, *Organization of Behavior* | Correlated firing strengthens synapses (postulate) | Bliss & Lømo (1973) supplied first physiological proof [2][8] |
| 1973 | Bliss & Lømo | LTP in hippocampal dentate gyrus | Frey & Morris (1997) explained synapse-specificity of LTP maintenance [10][19] |
| 1990s | Kandel lab (Aplysia CREB) | Molecular switch for long-term facilitation | Pittenger & Kandel (1998) generalized CREB switch to mammals [12][15] |
| 1997 | Frey & Morris | Synaptic tagging and capture | Moncada & Viola (2007/2009) extended STC to whole-animal behavioral tagging [20][23] |
| 2000 | Nader, Schafe & LeDoux | Memory reconsolidation | 2022 review linked reconsolidation to PTSD treatment and memory enhancement [28][29] |
| 2009 | Yang, Pan & Gan | Stable dendritic spines = lifelong memory | Albarran et al. (2021) found PirB as causal spine-stability lever [31][37] |
| 2012 | Liu, Ramirez et al. (Tonegawa) | Optogenetic engram identification | Uytiepo et al. (2025) revised engram connectivity via electron microscopy [40][45] |
| 2019/2023 | Klinzing/Born; Rasch/Born reviews | Sleep replay drives systems consolidation | 2025 Nature paper found intra-hippocampal (not just cortical) reorganization [47][50] |
| 2024–2026 | Zaki & Cai; Choucry et al.; Berdugo-Vega et al.; astroengram review | Engrams are dynamic, glial-inclusive, and reversible with decline | All three 2026 papers cite Tonegawa-lab optogenetic toolkit as shared methodological ancestor [44][46][54][56] |

## Implications for On-Demand Recall and Skill Acquisition

Across this 75-year arc, a consistent mechanistic chain emerges: Hebbian co-activation  produces LTP at the synaptic level; synaptic tagging and capture explains how weak, incidental experiences are retroactively or prospectively stabilized by co-occurring salient events; CREB-dependent transcription and local protein synthesis gate the transition from short-lived to lifelong synaptic change; and structural remodeling of dendritic spines provides the physical substrate for durable skill memory, though spine formation is a correlate rather than a strict requirement of retention. Retrieval itself is not a passive readout: reconsolidation research shows every act of recall re-opens the memory trace to modification, and the testing effect shows that this re-opening, if followed by successful re-encoding, produces the most durable form of learning — mechanistically resembling a second synaptic-tagging event. The newest research (2024–2026) suggests the brain's "on-demand" retrieval system is more distributed (engrams span neurons and astrocytes), more compositionally granular (sub-ensembles encode different moments within a single learning episode), and more reversible (silenced or aged engrams can be pharmacologically or genetically restored) than any single earlier paper anticipated.[2][28][23][58][19][51][54][56][57][31][33][8][10][16][12][53]
