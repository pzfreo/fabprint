"""Generate test fixture STL files."""

from pathlib import Path

import trimesh

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    """Generate small test STLs if they don't exist."""
    FIXTURES.mkdir(exist_ok=True)

    cube_path = FIXTURES / "cube_10mm.stl"
    if not cube_path.exists():
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.export(cube_path)

    cyl_path = FIXTURES / "cylinder_5x20mm.stl"
    if not cyl_path.exists():
        mesh = trimesh.creation.cylinder(radius=5, height=20)
        mesh.export(cyl_path)
