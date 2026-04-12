def build_ui_graph(reality,vision):
    return {
        "nodes":["hero","content","footer"],
        "edges":[["hero","content"],["content","footer"]]
    }
