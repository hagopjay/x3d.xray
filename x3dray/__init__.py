"""x3d.xray: exploratory data analysis whose output is an X3D world.

Pipeline: loaders -> profiler (no model) -> spec (the only place an AI may write)
-> compiler (plain code, writes X3D) -> gates -> receipt -> viewer.
"""

__version__ = "0.2.0.dev1"
