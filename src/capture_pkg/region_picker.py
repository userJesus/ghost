import tkinter as tk


def select_region() -> dict | None:
    """Open a fullscreen overlay for the user to drag-select a region.
    Returns {'x','y','w','h'} in screen coordinates, or None if cancelled."""
    result: dict = {}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.32)
    root.attributes("-topmost", True)
    root.configure(bg="black", cursor="cross")

    canvas = tk.Canvas(root, bg="black", highlightthickness=0, cursor="cross")
    canvas.pack(fill="both", expand=True)

    state = {"start_x": 0, "start_y": 0, "rect": None}

    def on_press(event):
        state["start_x"] = event.x_root
        state["start_y"] = event.y_root
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="#61dbb4", width=2, fill=""
        )

    def on_drag(event):
        if state["rect"]:
            start_local_x = state["start_x"] - root.winfo_rootx()
            start_local_y = state["start_y"] - root.winfo_rooty()
            canvas.coords(state["rect"], start_local_x, start_local_y, event.x, event.y)

    def on_release(event):
        result["x"] = min(state["start_x"], event.x_root)
        result["y"] = min(state["start_y"], event.y_root)
        result["w"] = abs(event.x_root - state["start_x"])
        result["h"] = abs(event.y_root - state["start_y"])
        root.destroy()

    def on_escape(_e):
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)

    root.mainloop()

    if "x" in result and result["w"] > 5 and result["h"] > 5:
        return result
    return None
