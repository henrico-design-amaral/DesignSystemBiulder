from layers.reality import capture_reality
from layers.vision import analyze_vision
from layers.graph import build_ui_graph
from layers.design import infer_design_system
from layers.fidelity import compute_fidelity

def run_pipeline(url):
    reality=capture_reality(url)
    vision=analyze_vision(reality)
    graph=build_ui_graph(reality,vision)
    design=infer_design_system(reality,vision,graph)
    fidelity=compute_fidelity(reality,vision,graph)

    return {
        "url":url,
        "reality":reality,
        "vision":vision,
        "graph":graph,
        "design_system":design,
        "fidelity":fidelity
    }
