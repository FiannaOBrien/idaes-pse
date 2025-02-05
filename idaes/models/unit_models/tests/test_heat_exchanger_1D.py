#################################################################################
# The Institute for the Design of Advanced Energy Systems Integrated Platform
# Framework (IDAES IP) was produced under the DOE Institute for the
# Design of Advanced Energy Systems (IDAES), and is copyright (c) 2018-2021
# by the software owners: The Regents of the University of California, through
# Lawrence Berkeley National Laboratory,  National Technology & Engineering
# Solutions of Sandia, LLC, Carnegie Mellon University, West Virginia University
# Research Corporation, et al.  All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and
# license information.
#################################################################################
"""
Tests for Heat Exchanger 1D unit model.

Author: Jaffer Ghouse
"""
import pytest
from io import StringIO

from pyomo.environ import (
    check_optimal_termination,
    ConcreteModel,
    value,
    units as pyunits,
)
from pyomo.common.config import ConfigBlock
from pyomo.util.check_units import assert_units_consistent, assert_units_equivalent

import idaes
from idaes.core import (
    FlowsheetBlock,
    MaterialBalanceType,
    EnergyBalanceType,
    MomentumBalanceType,
    useDefault,
)
from idaes.models.unit_models.heat_exchanger_1D import HeatExchanger1D as HX1D
from idaes.models.unit_models.heat_exchanger_1D import WallConductionType
from idaes.models.unit_models.heat_exchanger import HeatExchangerFlowPattern

from idaes.models.properties.modular_properties.base.generic_property import (
    GenericParameterBlock,
)
from idaes.models.properties.modular_properties.examples.BT_PR import configuration
from idaes.models.properties.activity_coeff_models.BTX_activity_coeff_VLE import (
    BTXParameterBlock,
)
from idaes.models.properties import iapws95
from idaes.models.properties.examples.saponification_thermo import (
    SaponificationParameterBlock,
)

from idaes.core.util.exceptions import ConfigurationError, InitializationError
from idaes.core.util.model_statistics import (
    degrees_of_freedom,
    number_variables,
    number_total_constraints,
    number_unused_variables,
)
from idaes.core.util.testing import PhysicalParameterTestBlock, initialization_tester
from idaes.core.util import scaling as iscale
from idaes.core.solvers import get_solver

# Imports to assemble BT-PR with different units
from idaes.core import LiquidPhase, VaporPhase, Component
from idaes.models.properties.modular_properties.state_definitions import FTPx
from idaes.models.properties.modular_properties.eos.ceos import Cubic, CubicType
from idaes.models.properties.modular_properties.phase_equil import SmoothVLE
from idaes.models.properties.modular_properties.phase_equil.bubble_dew import (
    LogBubbleDew,
)
from idaes.models.properties.modular_properties.phase_equil.forms import log_fugacity
import idaes.models.properties.modular_properties.pure.RPP4 as RPP

# -----------------------------------------------------------------------------
# Get default solver for testing
solver = get_solver()


# -----------------------------------------------------------------------------
@pytest.mark.unit
def test_config():
    m = ConcreteModel()
    m.fs = FlowsheetBlock(default={"dynamic": False})

    m.fs.properties = PhysicalParameterTestBlock()

    m.fs.unit = HX1D(
        default={
            "shell_side": {"property_package": m.fs.properties},
            "tube_side": {"property_package": m.fs.properties},
        }
    )

    # Check unit config arguments
    assert len(m.fs.unit.config) == 8
    assert isinstance(m.fs.unit.config.shell_side, ConfigBlock)
    assert isinstance(m.fs.unit.config.tube_side, ConfigBlock)
    assert m.fs.unit.config.flow_type == HeatExchangerFlowPattern.cocurrent
    assert m.fs.unit.config.has_wall_conduction == WallConductionType.zero_dimensional
    assert m.fs.unit.config.finite_elements == 20
    assert m.fs.unit.config.collocation_points == 5

    # Check shell side config arguments
    assert len(m.fs.unit.config.shell_side) == 11
    assert m.fs.unit.config.shell_side.dynamic == useDefault
    assert m.fs.unit.config.shell_side.has_holdup == useDefault
    assert (
        m.fs.unit.config.shell_side.material_balance_type
        == MaterialBalanceType.useDefault
    )
    assert (
        m.fs.unit.config.shell_side.energy_balance_type == EnergyBalanceType.useDefault
    )
    assert (
        m.fs.unit.config.shell_side.momentum_balance_type
        == MomentumBalanceType.pressureTotal
    )
    assert not m.fs.unit.config.shell_side.has_pressure_change
    assert not m.fs.unit.config.shell_side.has_phase_equilibrium
    assert m.fs.unit.config.shell_side.transformation_method == "dae.finite_difference"
    assert m.fs.unit.config.shell_side.transformation_scheme == "BACKWARD"

    # Check tube side config arguments
    assert len(m.fs.unit.config.tube_side) == 11
    assert m.fs.unit.config.tube_side.dynamic == useDefault
    assert m.fs.unit.config.tube_side.has_holdup == useDefault
    assert (
        m.fs.unit.config.tube_side.material_balance_type
        == MaterialBalanceType.useDefault
    )
    assert (
        m.fs.unit.config.tube_side.energy_balance_type == EnergyBalanceType.useDefault
    )
    assert (
        m.fs.unit.config.tube_side.momentum_balance_type
        == MomentumBalanceType.pressureTotal
    )
    assert not m.fs.unit.config.tube_side.has_pressure_change
    assert not m.fs.unit.config.tube_side.has_phase_equilibrium
    assert m.fs.unit.config.tube_side.transformation_method == "dae.finite_difference"
    assert m.fs.unit.config.tube_side.transformation_scheme == "BACKWARD"


@pytest.mark.unit
def test_config_validation():
    m = ConcreteModel()
    m.fs = FlowsheetBlock(default={"dynamic": False})

    m.fs.properties = BTXParameterBlock(default={"valid_phase": "Liq"})

    with pytest.raises(ConfigurationError):
        m.fs.HX_co_current = HX1D(
            default={
                "shell_side": {
                    "property_package": m.fs.properties,
                    "transformation_scheme": "BACKWARD",
                },
                "tube_side": {
                    "property_package": m.fs.properties,
                    "transformation_scheme": "FORWARD",
                },
                "flow_type": HeatExchangerFlowPattern.cocurrent,
            }
        )

    with pytest.raises(ConfigurationError):
        m.fs.HX_counter_current = HX1D(
            default={
                "shell_side": {
                    "property_package": m.fs.properties,
                    "transformation_method": "dae.finite_difference",
                },
                "tube_side": {
                    "property_package": m.fs.properties,
                    "transformation_method": "dae.collocation",
                },
                "flow_type": HeatExchangerFlowPattern.countercurrent,
            }
        )


# -----------------------------------------------------------------------------
class TestBTX_cocurrent(object):
    @pytest.fixture(scope="class")
    def btx(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        m.fs.properties = BTXParameterBlock(default={"valid_phase": "Liq"})

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties},
                "flow_type": HeatExchangerFlowPattern.cocurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_mol[0].fix(5)  # mol/s
        m.fs.unit.shell_inlet.temperature[0].fix(365)  # K
        m.fs.unit.shell_inlet.pressure[0].fix(101325)  # Pa
        m.fs.unit.shell_inlet.mole_frac_comp[0, "benzene"].fix(0.5)
        m.fs.unit.shell_inlet.mole_frac_comp[0, "toluene"].fix(0.5)

        m.fs.unit.tube_inlet.flow_mol[0].fix(1)  # mol/s
        m.fs.unit.tube_inlet.temperature[0].fix(300)  # K
        m.fs.unit.tube_inlet.pressure[0].fix(101325)  # Pa
        m.fs.unit.tube_inlet.mole_frac_comp[0, "benzene"].fix(0.5)
        m.fs.unit.tube_inlet.mole_frac_comp[0, "toluene"].fix(0.5)

        iscale.calculate_scaling_factors(m)

        return m

    @pytest.mark.unit
    def test_build(self, btx):
        assert hasattr(btx.fs.unit, "shell_inlet")
        assert len(btx.fs.unit.shell_inlet.vars) == 4
        assert hasattr(btx.fs.unit.shell_inlet, "flow_mol")
        assert hasattr(btx.fs.unit.shell_inlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.shell_inlet, "temperature")
        assert hasattr(btx.fs.unit.shell_inlet, "pressure")

        assert hasattr(btx.fs.unit, "tube_inlet")
        assert len(btx.fs.unit.tube_inlet.vars) == 4
        assert hasattr(btx.fs.unit.tube_inlet, "flow_mol")
        assert hasattr(btx.fs.unit.tube_inlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.tube_inlet, "temperature")
        assert hasattr(btx.fs.unit.tube_inlet, "pressure")

        assert hasattr(btx.fs.unit, "shell_outlet")
        assert len(btx.fs.unit.shell_outlet.vars) == 4
        assert hasattr(btx.fs.unit.shell_outlet, "flow_mol")
        assert hasattr(btx.fs.unit.shell_outlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.shell_outlet, "temperature")
        assert hasattr(btx.fs.unit.shell_outlet, "pressure")

        assert hasattr(btx.fs.unit, "tube_outlet")
        assert len(btx.fs.unit.tube_outlet.vars) == 4
        assert hasattr(btx.fs.unit.tube_outlet, "flow_mol")
        assert hasattr(btx.fs.unit.tube_outlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.tube_outlet, "temperature")
        assert hasattr(btx.fs.unit.tube_outlet, "pressure")

        assert hasattr(btx.fs.unit, "shell_area")
        assert hasattr(btx.fs.unit, "shell_length")
        assert hasattr(btx.fs.unit, "tube_area")
        assert hasattr(btx.fs.unit, "tube_length")
        assert hasattr(btx.fs.unit, "d_shell")
        assert hasattr(btx.fs.unit, "d_tube_outer")
        assert hasattr(btx.fs.unit, "d_tube_inner")
        assert hasattr(btx.fs.unit, "N_tubes")
        assert hasattr(btx.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(btx.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(btx.fs.unit, "temperature_wall")
        assert hasattr(btx.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(btx.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(btx.fs.unit, "wall_0D_model")
        assert hasattr(btx.fs.unit, "area_calc_tube")
        assert hasattr(btx.fs.unit, "area_calc_shell")

        assert number_variables(btx) == 869
        assert number_total_constraints(btx) == 803
        assert number_unused_variables(btx) == 8

    @pytest.mark.integration
    def test_units(self, btx):
        assert_units_equivalent(btx.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(btx.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(btx.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(btx.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(btx.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            btx.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            btx.fs.unit.tube_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.degK,
        )
        assert_units_equivalent(btx.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(btx)

    @pytest.mark.unit
    def test_dof(self, btx):
        assert degrees_of_freedom(btx) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, btx):
        perf_dict = btx.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": btx.fs.unit.N_tubes,
                "Shell Area": btx.fs.unit.shell.area,
                "Shell Diameter": btx.fs.unit.d_shell,
                "Shell Length": btx.fs.unit.shell.length,
                "Tube Area": btx.fs.unit.tube.area,
                "Tube Inner Diameter": btx.fs.unit.d_tube_inner,
                "Tube Length": btx.fs.unit.tube.length,
                "Tube Outer Diameter": btx.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, btx):
        stable = btx.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'flow_mol': getattr(pyunits.pint_registry, "mole/second"),
                'mole_frac_comp benzene': getattr(pyunits.pint_registry, "dimensionless"),
                'mole_frac_comp toluene': getattr(pyunits.pint_registry, "dimensionless"),
                'temperature': getattr(pyunits.pint_registry, "kelvin"),
                'pressure': getattr(pyunits.pint_registry, "Pa")},
            'Shell Inlet': {
                'flow_mol': pytest.approx(5.0, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(365, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)},
            'Shell Outlet': {
                'flow_mol': pytest.approx(1, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(298.15, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)},
            'Tube Inlet': {
                'flow_mol': pytest.approx(1.0, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(300, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)},
            'Tube Outlet': {
                'flow_mol': pytest.approx(1, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(298.15, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.component
    @pytest.mark.skipif(solver is None, reason="Solver not available")
    def test_initialize(self, btx):
        initialization_tester(btx)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solve(self, btx):
        results = solver.solve(btx)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, btx):
        assert pytest.approx(5, abs=1e-3) == value(btx.fs.unit.shell_outlet.flow_mol[0])
        assert pytest.approx(322.669, abs=1e-3) == value(
            btx.fs.unit.shell_outlet.temperature[0]
        )
        assert pytest.approx(101325, abs=1e-3) == value(
            btx.fs.unit.shell_outlet.pressure[0]
        )

        assert pytest.approx(1, abs=1e-3) == value(btx.fs.unit.tube_outlet.flow_mol[0])
        assert pytest.approx(322.463, abs=1e-3) == value(
            btx.fs.unit.tube_outlet.temperature[0]
        )
        assert pytest.approx(101325, abs=1e-3) == value(
            btx.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_conservation(self, btx):
        assert (
            abs(
                value(
                    btx.fs.unit.shell_inlet.flow_mol[0]
                    - btx.fs.unit.shell_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )
        assert (
            abs(
                value(
                    btx.fs.unit.tube_inlet.flow_mol[0]
                    - btx.fs.unit.tube_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )

        shell_side = value(
            btx.fs.unit.shell_outlet.flow_mol[0]
            * (
                btx.fs.unit.shell.properties[0, 0].enth_mol_phase["Liq"]
                - btx.fs.unit.shell.properties[0, 1].enth_mol_phase["Liq"]
            )
        )
        tube_side = value(
            btx.fs.unit.tube_outlet.flow_mol[0]
            * btx.fs.unit.N_tubes
            * (
                btx.fs.unit.tube.properties[0, 1].enth_mol_phase["Liq"]
                - btx.fs.unit.tube.properties[0, 0].enth_mol_phase["Liq"]
            )
        )
        assert abs(shell_side - tube_side) <= 1e-6


# -----------------------------------------------------------------------------
class TestBTX_countercurrent(object):
    @pytest.fixture(scope="class")
    def btx(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        m.fs.properties = BTXParameterBlock(default={"valid_phase": "Liq"})

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties},
                "flow_type": HeatExchangerFlowPattern.countercurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_mol[0].fix(5)  # mol/s
        m.fs.unit.shell_inlet.temperature[0].fix(365)  # K
        m.fs.unit.shell_inlet.pressure[0].fix(101325)  # Pa
        m.fs.unit.shell_inlet.mole_frac_comp[0, "benzene"].fix(0.5)
        m.fs.unit.shell_inlet.mole_frac_comp[0, "toluene"].fix(0.5)

        m.fs.unit.tube_inlet.flow_mol[0].fix(1)  # mol/s
        m.fs.unit.tube_inlet.temperature[0].fix(300)  # K
        m.fs.unit.tube_inlet.pressure[0].fix(101325)  # Pa
        m.fs.unit.tube_inlet.mole_frac_comp[0, "benzene"].fix(0.5)
        m.fs.unit.tube_inlet.mole_frac_comp[0, "toluene"].fix(0.5)

        iscale.calculate_scaling_factors(m.fs.unit)

        return m

    @pytest.mark.unit
    def test_build(self, btx):
        assert hasattr(btx.fs.unit, "shell_inlet")
        assert len(btx.fs.unit.shell_inlet.vars) == 4
        assert hasattr(btx.fs.unit.shell_inlet, "flow_mol")
        assert hasattr(btx.fs.unit.shell_inlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.shell_inlet, "temperature")
        assert hasattr(btx.fs.unit.shell_inlet, "pressure")

        assert hasattr(btx.fs.unit, "tube_inlet")
        assert len(btx.fs.unit.tube_inlet.vars) == 4
        assert hasattr(btx.fs.unit.tube_inlet, "flow_mol")
        assert hasattr(btx.fs.unit.tube_inlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.tube_inlet, "temperature")
        assert hasattr(btx.fs.unit.tube_inlet, "pressure")

        assert hasattr(btx.fs.unit, "shell_outlet")
        assert len(btx.fs.unit.shell_outlet.vars) == 4
        assert hasattr(btx.fs.unit.shell_outlet, "flow_mol")
        assert hasattr(btx.fs.unit.shell_outlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.shell_outlet, "temperature")
        assert hasattr(btx.fs.unit.shell_outlet, "pressure")

        assert hasattr(btx.fs.unit, "tube_outlet")
        assert len(btx.fs.unit.tube_outlet.vars) == 4
        assert hasattr(btx.fs.unit.tube_outlet, "flow_mol")
        assert hasattr(btx.fs.unit.tube_outlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.tube_outlet, "temperature")
        assert hasattr(btx.fs.unit.tube_outlet, "pressure")

        assert hasattr(btx.fs.unit, "shell_area")
        assert hasattr(btx.fs.unit, "shell_length")
        assert hasattr(btx.fs.unit, "tube_area")
        assert hasattr(btx.fs.unit, "tube_length")
        assert hasattr(btx.fs.unit, "d_shell")
        assert hasattr(btx.fs.unit, "d_tube_outer")
        assert hasattr(btx.fs.unit, "d_tube_inner")
        assert hasattr(btx.fs.unit, "N_tubes")
        assert hasattr(btx.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(btx.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(btx.fs.unit, "temperature_wall")
        assert hasattr(btx.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(btx.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(btx.fs.unit, "wall_0D_model")
        assert hasattr(btx.fs.unit, "area_calc_tube")
        assert hasattr(btx.fs.unit, "area_calc_shell")

        assert number_variables(btx) == 869
        assert number_total_constraints(btx) == 803
        assert number_unused_variables(btx) == 8

    @pytest.mark.integration
    def test_units(self, btx):
        assert_units_equivalent(btx.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(btx.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(btx.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(btx.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(btx.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            btx.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            btx.fs.unit.tube_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(btx.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(btx)

    @pytest.mark.unit
    def test_dof(self, btx):
        assert degrees_of_freedom(btx) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, btx):
        perf_dict = btx.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": btx.fs.unit.N_tubes,
                "Shell Area": btx.fs.unit.shell.area,
                "Shell Diameter": btx.fs.unit.d_shell,
                "Shell Length": btx.fs.unit.shell.length,
                "Tube Area": btx.fs.unit.tube.area,
                "Tube Inner Diameter": btx.fs.unit.d_tube_inner,
                "Tube Length": btx.fs.unit.tube.length,
                "Tube Outer Diameter": btx.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, btx):
        stable = btx.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'flow_mol': getattr(pyunits.pint_registry, "mole/second"),
                'mole_frac_comp benzene': getattr(pyunits.pint_registry, "dimensionless"),
                'mole_frac_comp toluene': getattr(pyunits.pint_registry, "dimensionless"),
                'temperature': getattr(pyunits.pint_registry, "kelvin"),
                'pressure': getattr(pyunits.pint_registry, "Pa")},
            'Shell Inlet': {
                'flow_mol': pytest.approx(5.0, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(365, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)},
            'Shell Outlet': {
                'flow_mol': pytest.approx(1, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(298.15, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)},
            'Tube Inlet': {
                'flow_mol': pytest.approx(1.0, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(300, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)},
            'Tube Outlet': {
                'flow_mol': pytest.approx(1, rel=1e-4),
                'mole_frac_comp benzene': pytest.approx(0.5, rel=1e-4),
                'mole_frac_comp toluene': pytest.approx(0.5, rel=1e-4),
                'temperature': pytest.approx(298.15, rel=1e-4),
                'pressure': pytest.approx(101325.0, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_initialize(self, btx):
        initialization_tester(
            btx,
            optarg={"tol": 1e-6},
            shell_state_args={"flow_mol": 5, "temperature": 304, "pressure": 101325},
            tube_state_args={"flow_mol": 1, "temperature": 331.5, "pressure": 101325},
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solve(self, btx):
        results = solver.solve(btx)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, btx):
        assert pytest.approx(5, abs=1e-3) == value(btx.fs.unit.shell_outlet.flow_mol[0])
        assert pytest.approx(304.292, abs=1e-3) == value(
            btx.fs.unit.shell_outlet.temperature[0]
        )
        assert pytest.approx(101325, abs=1e-3) == value(
            btx.fs.unit.shell_outlet.pressure[0]
        )

        assert pytest.approx(1, abs=1e-3) == value(btx.fs.unit.tube_outlet.flow_mol[0])
        assert pytest.approx(331.435, abs=1e-3) == value(
            btx.fs.unit.tube_outlet.temperature[0]
        )
        assert pytest.approx(101325, abs=1e-3) == value(
            btx.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_conservation(self, btx):
        assert (
            abs(
                value(
                    btx.fs.unit.shell_inlet.flow_mol[0]
                    - btx.fs.unit.shell_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )
        assert (
            abs(
                value(
                    btx.fs.unit.tube_inlet.flow_mol[0]
                    - btx.fs.unit.tube_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )

        shell_side = value(
            btx.fs.unit.shell_outlet.flow_mol[0]
            * (
                btx.fs.unit.shell.properties[0, 0].enth_mol_phase["Liq"]
                - btx.fs.unit.shell.properties[0, 1].enth_mol_phase["Liq"]
            )
        )
        tube_side = value(
            btx.fs.unit.tube_outlet.flow_mol[0]
            * btx.fs.unit.N_tubes
            * (
                btx.fs.unit.tube.properties[0, 0].enth_mol_phase["Liq"]
                - btx.fs.unit.tube.properties[0, 1].enth_mol_phase["Liq"]
            )
        )
        assert abs(shell_side - tube_side) <= 1e-6


# -----------------------------------------------------------------------------
@pytest.mark.iapws
@pytest.mark.skipif(not iapws95.iapws95_available(), reason="IAPWS not available")
class TestIAPWS_cocurrent(object):
    @pytest.fixture(scope="class")
    def iapws(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        m.fs.properties = iapws95.Iapws95ParameterBlock(
            default={"phase_presentation": iapws95.PhaseType.LG}
        )

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties},
                "flow_type": HeatExchangerFlowPattern.cocurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_mol[0].fix(5)
        m.fs.unit.shell_inlet.enth_mol[0].fix(50000)
        m.fs.unit.shell_inlet.pressure[0].fix(101325)

        m.fs.unit.tube_inlet.flow_mol[0].fix(5)
        m.fs.unit.tube_inlet.enth_mol[0].fix(7000)
        m.fs.unit.tube_inlet.pressure[0].fix(101325)

        return m

    @pytest.mark.unit
    def test_build(self, iapws):
        assert len(iapws.fs.unit.shell_inlet.vars) == 3
        assert hasattr(iapws.fs.unit.shell_inlet, "flow_mol")
        assert hasattr(iapws.fs.unit.shell_inlet, "enth_mol")
        assert hasattr(iapws.fs.unit.shell_inlet, "pressure")

        assert hasattr(iapws.fs.unit, "shell_outlet")
        assert len(iapws.fs.unit.shell_outlet.vars) == 3
        assert hasattr(iapws.fs.unit.shell_outlet, "flow_mol")
        assert hasattr(iapws.fs.unit.shell_outlet, "enth_mol")
        assert hasattr(iapws.fs.unit.shell_outlet, "pressure")

        assert len(iapws.fs.unit.tube_inlet.vars) == 3
        assert hasattr(iapws.fs.unit.tube_inlet, "flow_mol")
        assert hasattr(iapws.fs.unit.tube_inlet, "enth_mol")
        assert hasattr(iapws.fs.unit.tube_inlet, "pressure")

        assert hasattr(iapws.fs.unit, "tube_outlet")
        assert len(iapws.fs.unit.tube_outlet.vars) == 3
        assert hasattr(iapws.fs.unit.tube_outlet, "flow_mol")
        assert hasattr(iapws.fs.unit.tube_outlet, "enth_mol")
        assert hasattr(iapws.fs.unit.tube_outlet, "pressure")

        assert hasattr(iapws.fs.unit, "shell_area")
        assert hasattr(iapws.fs.unit, "shell_length")
        assert hasattr(iapws.fs.unit, "tube_area")
        assert hasattr(iapws.fs.unit, "tube_length")
        assert hasattr(iapws.fs.unit, "d_shell")
        assert hasattr(iapws.fs.unit, "d_tube_outer")
        assert hasattr(iapws.fs.unit, "d_tube_inner")
        assert hasattr(iapws.fs.unit, "N_tubes")
        assert hasattr(iapws.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(iapws.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(iapws.fs.unit, "temperature_wall")
        assert hasattr(iapws.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(iapws.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(iapws.fs.unit, "wall_0D_model")
        assert hasattr(iapws.fs.unit, "area_calc_tube")
        assert hasattr(iapws.fs.unit, "area_calc_shell")

        assert number_variables(iapws) == 617
        assert number_total_constraints(iapws) == 553
        assert number_unused_variables(iapws) == 10

    @pytest.mark.integration
    def test_units(self, iapws):
        assert_units_equivalent(iapws.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(iapws.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(iapws.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            iapws.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            iapws.fs.unit.tube_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(iapws.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(iapws)

    @pytest.mark.unit
    def test_dof(self, iapws):
        assert degrees_of_freedom(iapws) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, iapws):
        perf_dict = iapws.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": iapws.fs.unit.N_tubes,
                "Shell Area": iapws.fs.unit.shell.area,
                "Shell Diameter": iapws.fs.unit.d_shell,
                "Shell Length": iapws.fs.unit.shell.length,
                "Tube Area": iapws.fs.unit.tube.area,
                "Tube Inner Diameter": iapws.fs.unit.d_tube_inner,
                "Tube Length": iapws.fs.unit.tube.length,
                "Tube Outer Diameter": iapws.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, iapws):
        stable = iapws.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'Molar Flow (mol/s)': getattr(pyunits.pint_registry, "mole/second"),
                'Mass Flow (kg/s)': getattr(pyunits.pint_registry, "kg/second"),
                'T (K)': getattr(pyunits.pint_registry, "K"),
                'P (Pa)': getattr(pyunits.pint_registry, "Pa"),
                'Vapor Fraction': getattr(pyunits.pint_registry, "dimensionless"),
                'Molar Enthalpy (J/mol) Vap': getattr(pyunits.pint_registry, "J/mole"),
                'Molar Enthalpy (J/mol) Liq': getattr(pyunits.pint_registry, "J/mole")},
            'Shell Inlet': {
                'Molar Flow (mol/s)': pytest.approx(5, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(0.090076, rel=1e-4),
                'T (K)': pytest.approx(422.6, rel=1e-4),
                'P (Pa)': pytest.approx(101325, rel=1e-4),
                'Vapor Fraction': pytest.approx(1, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(50000, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(11342, rel=1e-4)},
            'Shell Outlet': {
                'Molar Flow (mol/s)': pytest.approx(1, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(1.8015e-2, rel=1e-4),
                'T (K)': pytest.approx(286.34, rel=1e-4),
                'P (Pa)': pytest.approx(1e5, rel=1e-4),
                'Vapor Fraction': pytest.approx(0, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(2168.6, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(1000, rel=1e-4)},
            'Tube Inlet': {
                'Molar Flow (mol/s)': pytest.approx(5, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(0.090076, rel=1e-4),
                'T (K)': pytest.approx(365.88, rel=1e-4),
                'P (Pa)': pytest.approx(101325, rel=1e-4),
                'Vapor Fraction': pytest.approx(0, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(47926, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(7000, rel=1e-4)},
            'Tube Outlet': {
                'Molar Flow (mol/s)': pytest.approx(1, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(1.8015e-2, rel=1e-4),
                'T (K)': pytest.approx(286.34, rel=1e-4),
                'P (Pa)': pytest.approx(1e5, rel=1e-4),
                'Vapor Fraction': pytest.approx(0, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(2168.6, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(1000, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_initialize(self, iapws):
        initialization_tester(iapws)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.unit
    def test_solve(self, iapws):
        results = solver.solve(iapws)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, iapws):
        assert pytest.approx(5, abs=1e-5) == value(
            iapws.fs.unit.shell_outlet.flow_mol[0]
        )
        assert pytest.approx(5, abs=1e-5) == value(
            iapws.fs.unit.tube_outlet.flow_mol[0]
        )

        assert pytest.approx(46298, abs=4e0) == value(
            iapws.fs.unit.shell_outlet.enth_mol[0]
        )
        assert pytest.approx(7370, abs=1e0) == value(
            iapws.fs.unit.tube_outlet.enth_mol[0]
        )

        assert pytest.approx(101325, abs=1e2) == value(
            iapws.fs.unit.shell_outlet.pressure[0]
        )
        assert pytest.approx(101325, abs=1e2) == value(
            iapws.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_conservation(self, iapws):
        assert (
            abs(
                value(
                    iapws.fs.unit.shell_inlet.flow_mol[0]
                    - iapws.fs.unit.shell_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )
        assert (
            abs(
                value(
                    iapws.fs.unit.tube_inlet.flow_mol[0]
                    - iapws.fs.unit.tube_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )

        shell_side = value(
            iapws.fs.unit.shell_outlet.flow_mol[0]
            * (
                iapws.fs.unit.shell_inlet.enth_mol[0]
                - iapws.fs.unit.shell_outlet.enth_mol[0]
            )
        )
        tube_side = value(
            iapws.fs.unit.tube_outlet.flow_mol[0]
            * iapws.fs.unit.N_tubes
            * (
                iapws.fs.unit.tube_inlet.enth_mol[0]
                - iapws.fs.unit.tube_outlet.enth_mol[0]
            )
        )
        assert abs(shell_side + tube_side) <= 1e-6


# -----------------------------------------------------------------------------
@pytest.mark.iapws
@pytest.mark.skipif(not iapws95.iapws95_available(), reason="IAPWS not available")
class TestIAPWS_countercurrent(object):
    @pytest.fixture(scope="class")
    def iapws(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        m.fs.properties = iapws95.Iapws95ParameterBlock(
            default={"phase_presentation": iapws95.PhaseType.LG}
        )

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties},
                "flow_type": HeatExchangerFlowPattern.countercurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_mol[0].fix(5)
        m.fs.unit.shell_inlet.enth_mol[0].fix(50000)
        m.fs.unit.shell_inlet.pressure[0].fix(101325)

        m.fs.unit.tube_inlet.flow_mol[0].fix(5)
        m.fs.unit.tube_inlet.enth_mol[0].fix(7000)
        m.fs.unit.tube_inlet.pressure[0].fix(101325)

        return m

    @pytest.mark.unit
    def test_build(self, iapws):
        assert len(iapws.fs.unit.shell_inlet.vars) == 3
        assert hasattr(iapws.fs.unit.shell_inlet, "flow_mol")
        assert hasattr(iapws.fs.unit.shell_inlet, "enth_mol")
        assert hasattr(iapws.fs.unit.shell_inlet, "pressure")

        assert hasattr(iapws.fs.unit, "shell_outlet")
        assert len(iapws.fs.unit.shell_outlet.vars) == 3
        assert hasattr(iapws.fs.unit.shell_outlet, "flow_mol")
        assert hasattr(iapws.fs.unit.shell_outlet, "enth_mol")
        assert hasattr(iapws.fs.unit.shell_outlet, "pressure")

        assert len(iapws.fs.unit.tube_inlet.vars) == 3
        assert hasattr(iapws.fs.unit.tube_inlet, "flow_mol")
        assert hasattr(iapws.fs.unit.tube_inlet, "enth_mol")
        assert hasattr(iapws.fs.unit.tube_inlet, "pressure")

        assert hasattr(iapws.fs.unit, "tube_outlet")
        assert len(iapws.fs.unit.tube_outlet.vars) == 3
        assert hasattr(iapws.fs.unit.tube_outlet, "flow_mol")
        assert hasattr(iapws.fs.unit.tube_outlet, "enth_mol")
        assert hasattr(iapws.fs.unit.tube_outlet, "pressure")

        assert hasattr(iapws.fs.unit, "shell_area")
        assert hasattr(iapws.fs.unit, "shell_length")
        assert hasattr(iapws.fs.unit, "tube_area")
        assert hasattr(iapws.fs.unit, "tube_length")
        assert hasattr(iapws.fs.unit, "d_shell")
        assert hasattr(iapws.fs.unit, "d_tube_outer")
        assert hasattr(iapws.fs.unit, "d_tube_inner")
        assert hasattr(iapws.fs.unit, "N_tubes")
        assert hasattr(iapws.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(iapws.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(iapws.fs.unit, "temperature_wall")
        assert hasattr(iapws.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(iapws.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(iapws.fs.unit, "wall_0D_model")
        assert hasattr(iapws.fs.unit, "area_calc_tube")
        assert hasattr(iapws.fs.unit, "area_calc_shell")

        assert number_variables(iapws) == 617
        assert number_total_constraints(iapws) == 553
        assert number_unused_variables(iapws) == 10

    @pytest.mark.integration
    def test_units(self, iapws):
        assert_units_equivalent(iapws.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(iapws.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(iapws.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(iapws.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            iapws.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            iapws.fs.unit.tube_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(iapws.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(iapws)

    @pytest.mark.unit
    def test_dof(self, iapws):
        assert degrees_of_freedom(iapws) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, iapws):
        perf_dict = iapws.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": iapws.fs.unit.N_tubes,
                "Shell Area": iapws.fs.unit.shell.area,
                "Shell Diameter": iapws.fs.unit.d_shell,
                "Shell Length": iapws.fs.unit.shell.length,
                "Tube Area": iapws.fs.unit.tube.area,
                "Tube Inner Diameter": iapws.fs.unit.d_tube_inner,
                "Tube Length": iapws.fs.unit.tube.length,
                "Tube Outer Diameter": iapws.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, iapws):
        stable = iapws.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'Molar Flow (mol/s)': getattr(pyunits.pint_registry, "mole/second"),
                'Mass Flow (kg/s)': getattr(pyunits.pint_registry, "kg/second"),
                'T (K)': getattr(pyunits.pint_registry, "K"),
                'P (Pa)': getattr(pyunits.pint_registry, "Pa"),
                'Vapor Fraction': getattr(pyunits.pint_registry, "dimensionless"),
                'Molar Enthalpy (J/mol) Vap': getattr(pyunits.pint_registry, "J/mole"),
                'Molar Enthalpy (J/mol) Liq': getattr(pyunits.pint_registry, "J/mole")},
            'Shell Inlet': {
                'Molar Flow (mol/s)': pytest.approx(5, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(0.090076, rel=1e-4),
                'T (K)': pytest.approx(422.6, rel=1e-4),
                'P (Pa)': pytest.approx(101325, rel=1e-4),
                'Vapor Fraction': pytest.approx(1, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(50000, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(11342, rel=1e-4)},
            'Shell Outlet': {
                'Molar Flow (mol/s)': pytest.approx(1, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(1.8015e-2, rel=1e-4),
                'T (K)': pytest.approx(286.34, rel=1e-4),
                'P (Pa)': pytest.approx(1e5, rel=1e-4),
                'Vapor Fraction': pytest.approx(0, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(2168.6, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(1000, rel=1e-4)},
            'Tube Inlet': {
                'Molar Flow (mol/s)': pytest.approx(5, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(0.090076, rel=1e-4),
                'T (K)': pytest.approx(365.88, rel=1e-4),
                'P (Pa)': pytest.approx(101325, rel=1e-4),
                'Vapor Fraction': pytest.approx(0, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(47926, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(7000, rel=1e-4)},
            'Tube Outlet': {
                'Molar Flow (mol/s)': pytest.approx(1, rel=1e-4),
                'Mass Flow (kg/s)': pytest.approx(1.8015e-2, rel=1e-4),
                'T (K)': pytest.approx(286.34, rel=1e-4),
                'P (Pa)': pytest.approx(1e5, rel=1e-4),
                'Vapor Fraction': pytest.approx(0, abs=1e-4),
                'Molar Enthalpy (J/mol) Vap': pytest.approx(2168.6, rel=1e-4),
                'Molar Enthalpy (J/mol) Liq': pytest.approx(1000, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_initialize(self, iapws):
        initialization_tester(iapws)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solve(self, iapws):
        results = solver.solve(iapws)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, iapws):
        assert pytest.approx(5, abs=1e-5) == value(
            iapws.fs.unit.shell_outlet.flow_mol[0]
        )
        assert pytest.approx(5, abs=1e-5) == value(
            iapws.fs.unit.tube_outlet.flow_mol[0]
        )

        assert pytest.approx(45359, abs=1e0) == value(
            iapws.fs.unit.shell_outlet.enth_mol[0]
        )
        assert pytest.approx(7464, abs=1e0) == value(
            iapws.fs.unit.tube_outlet.enth_mol[0]
        )

        assert pytest.approx(101325, abs=1e2) == value(
            iapws.fs.unit.shell_outlet.pressure[0]
        )
        assert pytest.approx(101325, abs=1e2) == value(
            iapws.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_conservation(self, iapws):
        assert (
            abs(
                value(
                    iapws.fs.unit.shell_inlet.flow_mol[0]
                    - iapws.fs.unit.shell_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )
        assert (
            abs(
                value(
                    iapws.fs.unit.tube_inlet.flow_mol[0]
                    - iapws.fs.unit.tube_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )

        shell_side = value(
            iapws.fs.unit.shell_outlet.flow_mol[0]
            * (
                iapws.fs.unit.shell_inlet.enth_mol[0]
                - iapws.fs.unit.shell_outlet.enth_mol[0]
            )
        )
        tube_side = value(
            iapws.fs.unit.tube_outlet.flow_mol[0]
            * iapws.fs.unit.N_tubes
            * (
                iapws.fs.unit.tube_inlet.enth_mol[0]
                - iapws.fs.unit.tube_outlet.enth_mol[0]
            )
        )
        assert abs(shell_side + tube_side) <= 1e-6


# -----------------------------------------------------------------------------
class TestSaponification_cocurrent(object):
    @pytest.fixture(scope="class")
    def sapon(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        m.fs.properties = SaponificationParameterBlock()

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties},
                "flow_type": HeatExchangerFlowPattern.cocurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_vol[0].fix(1e-3)
        m.fs.unit.shell_inlet.temperature[0].fix(320)
        m.fs.unit.shell_inlet.pressure[0].fix(101325)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "H2O"].fix(55388.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "NaOH"].fix(100.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "EthylAcetate"].fix(100.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "SodiumAcetate"].fix(0.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "Ethanol"].fix(0.0)

        m.fs.unit.tube_inlet.flow_vol[0].fix(1e-3)
        m.fs.unit.tube_inlet.temperature[0].fix(300)
        m.fs.unit.tube_inlet.pressure[0].fix(101325)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "H2O"].fix(55388.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "NaOH"].fix(100.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "EthylAcetate"].fix(100.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "SodiumAcetate"].fix(0.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "Ethanol"].fix(0.0)

        return m

    @pytest.mark.unit
    def test_build(self, sapon):
        assert len(sapon.fs.unit.shell_inlet.vars) == 4
        assert hasattr(sapon.fs.unit.shell_inlet, "flow_vol")
        assert hasattr(sapon.fs.unit.shell_inlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.shell_inlet, "temperature")
        assert hasattr(sapon.fs.unit.shell_inlet, "pressure")

        assert len(sapon.fs.unit.shell_outlet.vars) == 4
        assert hasattr(sapon.fs.unit.shell_outlet, "flow_vol")
        assert hasattr(sapon.fs.unit.shell_outlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.shell_outlet, "temperature")
        assert hasattr(sapon.fs.unit.shell_outlet, "pressure")

        assert len(sapon.fs.unit.tube_inlet.vars) == 4
        assert hasattr(sapon.fs.unit.tube_inlet, "flow_vol")
        assert hasattr(sapon.fs.unit.tube_inlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.tube_inlet, "temperature")
        assert hasattr(sapon.fs.unit.tube_inlet, "pressure")

        assert len(sapon.fs.unit.tube_outlet.vars) == 4
        assert hasattr(sapon.fs.unit.tube_outlet, "flow_vol")
        assert hasattr(sapon.fs.unit.tube_outlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.tube_outlet, "temperature")
        assert hasattr(sapon.fs.unit.tube_outlet, "pressure")

        assert hasattr(sapon.fs.unit, "shell_area")
        assert hasattr(sapon.fs.unit, "shell_length")
        assert hasattr(sapon.fs.unit, "tube_area")
        assert hasattr(sapon.fs.unit, "tube_length")
        assert hasattr(sapon.fs.unit, "d_shell")
        assert hasattr(sapon.fs.unit, "d_tube_outer")
        assert hasattr(sapon.fs.unit, "d_tube_inner")
        assert hasattr(sapon.fs.unit, "N_tubes")
        assert hasattr(sapon.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(sapon.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(sapon.fs.unit, "temperature_wall")
        assert hasattr(sapon.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(sapon.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(sapon.fs.unit, "wall_0D_model")
        assert hasattr(sapon.fs.unit, "area_calc_tube")
        assert hasattr(sapon.fs.unit, "area_calc_shell")

        assert number_variables(sapon) == 995
        assert number_total_constraints(sapon) == 917
        assert number_unused_variables(sapon) == 14

    @pytest.mark.integration
    def test_units(self, sapon):
        assert_units_equivalent(sapon.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(sapon.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(sapon.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            sapon.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            sapon.fs.unit.tube_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(sapon.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(sapon)

    @pytest.mark.unit
    def test_dof(self, sapon):
        assert degrees_of_freedom(sapon) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, sapon):
        perf_dict = sapon.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": sapon.fs.unit.N_tubes,
                "Shell Area": sapon.fs.unit.shell.area,
                "Shell Diameter": sapon.fs.unit.d_shell,
                "Shell Length": sapon.fs.unit.shell.length,
                "Tube Area": sapon.fs.unit.tube.area,
                "Tube Inner Diameter": sapon.fs.unit.d_tube_inner,
                "Tube Length": sapon.fs.unit.tube.length,
                "Tube Outer Diameter": sapon.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, sapon):
        stable = sapon.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'Volumetric Flowrate': getattr(pyunits.pint_registry, "m**3/second"),
                'Molar Concentration H2O': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration NaOH': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration EthylAcetate': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration SodiumAcetate': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration Ethanol': getattr(pyunits.pint_registry, "mole/m**3"),
                'Temperature': getattr(pyunits.pint_registry, "K"),
                'Pressure': getattr(pyunits.pint_registry, "Pa")},
            'Shell Inlet': {
                'Volumetric Flowrate': pytest.approx(1e-3, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(55388, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(0, abs=1e-4),
                'Molar Concentration Ethanol': pytest.approx(0, abs=1e-4),
                'Temperature': pytest.approx(320, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)},
            'Shell Outlet': {
                'Volumetric Flowrate': pytest.approx(1.00, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration Ethanol': pytest.approx(100.00, rel=1e-4),
                'Temperature': pytest.approx(298.15, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)},
            'Tube Inlet': {
                'Volumetric Flowrate': pytest.approx(1e-3, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(55388, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(0, abs=1e-4),
                'Molar Concentration Ethanol': pytest.approx(0, abs=1e-4),
                'Temperature': pytest.approx(300, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)},
            'Tube Outlet': {
                'Volumetric Flowrate': pytest.approx(1.00, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration Ethanol': pytest.approx(100.00, rel=1e-4),
                'Temperature': pytest.approx(298.15, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_initialize(self, sapon):
        initialization_tester(sapon)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solve(self, sapon):
        results = solver.solve(sapon)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, sapon):
        assert pytest.approx(1e-3, abs=1e-6) == value(
            sapon.fs.unit.shell_outlet.flow_vol[0]
        )
        assert pytest.approx(1e-3, abs=1e-6) == value(
            sapon.fs.unit.tube_outlet.flow_vol[0]
        )

        assert 55388.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "H2O"])
        assert 100.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "NaOH"])
        assert 100.0 == value(
            sapon.fs.unit.shell_inlet.conc_mol_comp[0, "EthylAcetate"]
        )
        assert 0.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "SodiumAcetate"])
        assert 0.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "Ethanol"])

        assert 55388.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "H2O"])
        assert 100.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "NaOH"])
        assert 100.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "EthylAcetate"])
        assert 0.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "SodiumAcetate"])
        assert 0.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "Ethanol"])

        assert pytest.approx(309.4, abs=1e-1) == value(
            sapon.fs.unit.shell_outlet.temperature[0]
        )
        assert pytest.approx(301.1, abs=1e-1) == value(
            sapon.fs.unit.tube_outlet.temperature[0]
        )

        assert pytest.approx(101325, abs=1e2) == value(
            sapon.fs.unit.shell_outlet.pressure[0]
        )
        assert pytest.approx(101325, abs=1e2) == value(
            sapon.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_conservation(self, sapon):
        shell_side = value(
            sapon.fs.unit.shell_outlet.flow_vol[0]
            * sapon.fs.properties.dens_mol
            * sapon.fs.properties.cp_mol
            * (
                sapon.fs.unit.shell_inlet.temperature[0]
                - sapon.fs.unit.shell_outlet.temperature[0]
            )
        )
        tube_side = value(
            sapon.fs.unit.tube_outlet.flow_vol[0]
            * sapon.fs.unit.N_tubes
            * sapon.fs.properties.dens_mol
            * sapon.fs.properties.cp_mol
            * (
                sapon.fs.unit.tube_inlet.temperature[0]
                - sapon.fs.unit.tube_outlet.temperature[0]
            )
        )
        assert abs(shell_side + tube_side) <= 1e-6


# -----------------------------------------------------------------------------
class TestSaponification_countercurrent(object):
    @pytest.fixture(scope="class")
    def sapon(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        m.fs.properties = SaponificationParameterBlock()

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties},
                "flow_type": HeatExchangerFlowPattern.countercurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_vol[0].fix(1e-3)
        m.fs.unit.shell_inlet.temperature[0].fix(320)
        m.fs.unit.shell_inlet.pressure[0].fix(101325)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "H2O"].fix(55388.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "NaOH"].fix(100.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "EthylAcetate"].fix(100.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "SodiumAcetate"].fix(0.0)
        m.fs.unit.shell_inlet.conc_mol_comp[0, "Ethanol"].fix(0.0)

        m.fs.unit.tube_inlet.flow_vol[0].fix(1e-3)
        m.fs.unit.tube_inlet.temperature[0].fix(300)
        m.fs.unit.tube_inlet.pressure[0].fix(101325)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "H2O"].fix(55388.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "NaOH"].fix(100.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "EthylAcetate"].fix(100.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "SodiumAcetate"].fix(0.0)
        m.fs.unit.tube_inlet.conc_mol_comp[0, "Ethanol"].fix(0.0)

        return m

    @pytest.mark.unit
    def test_build(self, sapon):
        assert len(sapon.fs.unit.shell_inlet.vars) == 4
        assert hasattr(sapon.fs.unit.shell_inlet, "flow_vol")
        assert hasattr(sapon.fs.unit.shell_inlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.shell_inlet, "temperature")
        assert hasattr(sapon.fs.unit.shell_inlet, "pressure")

        assert len(sapon.fs.unit.shell_outlet.vars) == 4
        assert hasattr(sapon.fs.unit.shell_outlet, "flow_vol")
        assert hasattr(sapon.fs.unit.shell_outlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.shell_outlet, "temperature")
        assert hasattr(sapon.fs.unit.shell_outlet, "pressure")

        assert len(sapon.fs.unit.tube_inlet.vars) == 4
        assert hasattr(sapon.fs.unit.tube_inlet, "flow_vol")
        assert hasattr(sapon.fs.unit.tube_inlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.tube_inlet, "temperature")
        assert hasattr(sapon.fs.unit.tube_inlet, "pressure")

        assert len(sapon.fs.unit.tube_outlet.vars) == 4
        assert hasattr(sapon.fs.unit.tube_outlet, "flow_vol")
        assert hasattr(sapon.fs.unit.tube_outlet, "conc_mol_comp")
        assert hasattr(sapon.fs.unit.tube_outlet, "temperature")
        assert hasattr(sapon.fs.unit.tube_outlet, "pressure")

        assert hasattr(sapon.fs.unit, "shell_area")
        assert hasattr(sapon.fs.unit, "shell_length")
        assert hasattr(sapon.fs.unit, "tube_area")
        assert hasattr(sapon.fs.unit, "tube_length")
        assert hasattr(sapon.fs.unit, "d_shell")
        assert hasattr(sapon.fs.unit, "d_tube_outer")
        assert hasattr(sapon.fs.unit, "d_tube_inner")
        assert hasattr(sapon.fs.unit, "N_tubes")
        assert hasattr(sapon.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(sapon.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(sapon.fs.unit, "temperature_wall")
        assert hasattr(sapon.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(sapon.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(sapon.fs.unit, "wall_0D_model")
        assert hasattr(sapon.fs.unit, "area_calc_tube")
        assert hasattr(sapon.fs.unit, "area_calc_shell")

        assert number_variables(sapon) == 995
        assert number_total_constraints(sapon) == 917
        assert number_unused_variables(sapon) == 14

    @pytest.mark.integration
    def test_units(self, sapon):
        assert_units_equivalent(sapon.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(sapon.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(sapon.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(sapon.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            sapon.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            sapon.fs.unit.tube_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(sapon.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(sapon)

    @pytest.mark.unit
    def test_dof(self, sapon):
        assert degrees_of_freedom(sapon) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, sapon):
        perf_dict = sapon.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": sapon.fs.unit.N_tubes,
                "Shell Area": sapon.fs.unit.shell.area,
                "Shell Diameter": sapon.fs.unit.d_shell,
                "Shell Length": sapon.fs.unit.shell.length,
                "Tube Area": sapon.fs.unit.tube.area,
                "Tube Inner Diameter": sapon.fs.unit.d_tube_inner,
                "Tube Length": sapon.fs.unit.tube.length,
                "Tube Outer Diameter": sapon.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, sapon):
        stable = sapon.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'Volumetric Flowrate': getattr(pyunits.pint_registry, "m**3/second"),
                'Molar Concentration H2O': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration NaOH': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration EthylAcetate': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration SodiumAcetate': getattr(pyunits.pint_registry, "mole/m**3"),
                'Molar Concentration Ethanol': getattr(pyunits.pint_registry, "mole/m**3"),
                'Temperature': getattr(pyunits.pint_registry, "K"),
                'Pressure': getattr(pyunits.pint_registry, "Pa")},
            'Shell Inlet': {
                'Volumetric Flowrate': pytest.approx(1e-3, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(55388, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(0, abs=1e-4),
                'Molar Concentration Ethanol': pytest.approx(0, abs=1e-4),
                'Temperature': pytest.approx(320, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)},
            'Shell Outlet': {
                'Volumetric Flowrate': pytest.approx(1.00, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration Ethanol': pytest.approx(100.00, rel=1e-4),
                'Temperature': pytest.approx(298.15, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)},
            'Tube Inlet': {
                'Volumetric Flowrate': pytest.approx(1e-3, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(55388, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(0, abs=1e-4),
                'Molar Concentration Ethanol': pytest.approx(0, abs=1e-4),
                'Temperature': pytest.approx(300, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)},
            'Tube Outlet': {
                'Volumetric Flowrate': pytest.approx(1.00, rel=1e-4),
                'Molar Concentration H2O': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration NaOH': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration EthylAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration SodiumAcetate': pytest.approx(100.00, rel=1e-4),
                'Molar Concentration Ethanol': pytest.approx(100.00, rel=1e-4),
                'Temperature': pytest.approx(298.15, rel=1e-4),
                'Pressure': pytest.approx(1.0132e+05, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_initialize(self, sapon):
        initialization_tester(sapon)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solve(self, sapon):
        results = solver.solve(sapon)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, sapon):
        assert pytest.approx(1e-3, abs=1e-6) == value(
            sapon.fs.unit.shell_outlet.flow_vol[0]
        )
        assert pytest.approx(1e-3, abs=1e-6) == value(
            sapon.fs.unit.tube_outlet.flow_vol[0]
        )

        assert 55388.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "H2O"])
        assert 100.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "NaOH"])
        assert 100.0 == value(
            sapon.fs.unit.shell_inlet.conc_mol_comp[0, "EthylAcetate"]
        )
        assert 0.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "SodiumAcetate"])
        assert 0.0 == value(sapon.fs.unit.shell_inlet.conc_mol_comp[0, "Ethanol"])

        assert 55388.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "H2O"])
        assert 100.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "NaOH"])
        assert 100.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "EthylAcetate"])
        assert 0.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "SodiumAcetate"])
        assert 0.0 == value(sapon.fs.unit.tube_inlet.conc_mol_comp[0, "Ethanol"])

        assert pytest.approx(309.2, abs=1e-1) == value(
            sapon.fs.unit.shell_outlet.temperature[0]
        )
        assert pytest.approx(301.1, abs=1e-1) == value(
            sapon.fs.unit.tube_outlet.temperature[0]
        )

        assert pytest.approx(101325, abs=1e2) == value(
            sapon.fs.unit.shell_outlet.pressure[0]
        )
        assert pytest.approx(101325, abs=1e2) == value(
            sapon.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_conservation(self, sapon):
        shell_side = value(
            sapon.fs.unit.shell_outlet.flow_vol[0]
            * sapon.fs.properties.dens_mol
            * sapon.fs.properties.cp_mol
            * (
                sapon.fs.unit.shell_inlet.temperature[0]
                - sapon.fs.unit.shell_outlet.temperature[0]
            )
        )
        tube_side = value(
            sapon.fs.unit.tube_outlet.flow_vol[0]
            * sapon.fs.unit.N_tubes
            * sapon.fs.properties.dens_mol
            * sapon.fs.properties.cp_mol
            * (
                sapon.fs.unit.tube_inlet.temperature[0]
                - sapon.fs.unit.tube_outlet.temperature[0]
            )
        )
        assert abs(shell_side + tube_side) <= 1e-6


# -----------------------------------------------------------------------------
class TestBT_Generic_cocurrent(object):
    @pytest.fixture(scope="class")
    def btx(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(default={"dynamic": False})

        # As we lack other example prop packs with units, take the generic
        # BT-PR package and change the base units
        configuration2 = {
            # Specifying components
            "components": {
                "benzene": {
                    "type": Component,
                    "enth_mol_ig_comp": RPP,
                    "entr_mol_ig_comp": RPP,
                    "pressure_sat_comp": RPP,
                    "phase_equilibrium_form": {("Vap", "Liq"): log_fugacity},
                    "parameter_data": {
                        "mw": (78.1136e-3, pyunits.kg / pyunits.mol),  # [1]
                        "pressure_crit": (48.9e5, pyunits.Pa),  # [1]
                        "temperature_crit": (562.2, pyunits.K),  # [1]
                        "omega": 0.212,  # [1]
                        "cp_mol_ig_comp_coeff": {
                            "A": (-3.392e1, pyunits.J / pyunits.mol / pyunits.K),  # [1]
                            "B": (4.739e-1, pyunits.J / pyunits.mol / pyunits.K**2),
                            "C": (-3.017e-4, pyunits.J / pyunits.mol / pyunits.K**3),
                            "D": (7.130e-8, pyunits.J / pyunits.mol / pyunits.K**4),
                        },
                        "enth_mol_form_vap_comp_ref": (
                            82.9e3,
                            pyunits.J / pyunits.mol,
                        ),  # [3]
                        "entr_mol_form_vap_comp_ref": (
                            -269,
                            pyunits.J / pyunits.mol / pyunits.K,
                        ),  # [3]
                        "pressure_sat_comp_coeff": {
                            "A": (-6.98273, None),  # [1]
                            "B": (1.33213, None),
                            "C": (-2.62863, None),
                            "D": (-3.33399, None),
                        },
                    },
                },
                "toluene": {
                    "type": Component,
                    "enth_mol_ig_comp": RPP,
                    "entr_mol_ig_comp": RPP,
                    "pressure_sat_comp": RPP,
                    "phase_equilibrium_form": {("Vap", "Liq"): log_fugacity},
                    "parameter_data": {
                        "mw": (92.1405e-3, pyunits.kg / pyunits.mol),  # [1]
                        "pressure_crit": (41e5, pyunits.Pa),  # [1]
                        "temperature_crit": (591.8, pyunits.K),  # [1]
                        "omega": 0.263,  # [1]
                        "cp_mol_ig_comp_coeff": {
                            "A": (-2.435e1, pyunits.J / pyunits.mol / pyunits.K),  # [1]
                            "B": (5.125e-1, pyunits.J / pyunits.mol / pyunits.K**2),
                            "C": (-2.765e-4, pyunits.J / pyunits.mol / pyunits.K**3),
                            "D": (4.911e-8, pyunits.J / pyunits.mol / pyunits.K**4),
                        },
                        "enth_mol_form_vap_comp_ref": (
                            50.1e3,
                            pyunits.J / pyunits.mol,
                        ),  # [3]
                        "entr_mol_form_vap_comp_ref": (
                            -321,
                            pyunits.J / pyunits.mol / pyunits.K,
                        ),  # [3]
                        "pressure_sat_comp_coeff": {
                            "A": (-7.28607, None),  # [1]
                            "B": (1.38091, None),
                            "C": (-2.83433, None),
                            "D": (-2.79168, None),
                        },
                    },
                },
            },
            # Specifying phases
            "phases": {
                "Liq": {
                    "type": LiquidPhase,
                    "equation_of_state": Cubic,
                    "equation_of_state_options": {"type": CubicType.PR},
                },
                "Vap": {
                    "type": VaporPhase,
                    "equation_of_state": Cubic,
                    "equation_of_state_options": {"type": CubicType.PR},
                },
            },
            # Set base units of measurement
            "base_units": {
                "time": pyunits.s,
                "length": pyunits.m,
                "mass": pyunits.t,
                "amount": pyunits.mol,
                "temperature": pyunits.degR,
            },
            # Specifying state definition
            "state_definition": FTPx,
            "state_bounds": {
                "flow_mol": (0, 100, 1000, pyunits.mol / pyunits.s),
                "temperature": (273.15, 300, 500, pyunits.K),
                "pressure": (5e4, 1e5, 1e6, pyunits.Pa),
            },
            "pressure_ref": (101325, pyunits.Pa),
            "temperature_ref": (298.15, pyunits.K),
            # Defining phase equilibria
            "phases_in_equilibrium": [("Vap", "Liq")],
            "phase_equilibrium_state": {("Vap", "Liq"): SmoothVLE},
            "bubble_dew_method": LogBubbleDew,
            "parameter_data": {
                "PR_kappa": {
                    ("benzene", "benzene"): 0.000,
                    ("benzene", "toluene"): 0.000,
                    ("toluene", "benzene"): 0.000,
                    ("toluene", "toluene"): 0.000,
                }
            },
        }

        m.fs.properties = GenericParameterBlock(default=configuration)
        m.fs.properties2 = GenericParameterBlock(default=configuration2)

        m.fs.unit = HX1D(
            default={
                "shell_side": {"property_package": m.fs.properties},
                "tube_side": {"property_package": m.fs.properties2},
                "flow_type": HeatExchangerFlowPattern.cocurrent,
            }
        )

        m.fs.unit.d_shell.fix(1.04)
        m.fs.unit.d_tube_outer.fix(0.01167)
        m.fs.unit.d_tube_inner.fix(0.01067)
        m.fs.unit.N_tubes.fix(10)
        m.fs.unit.shell_length.fix(4.85)
        m.fs.unit.tube_length.fix(4.85)
        m.fs.unit.shell_heat_transfer_coefficient.fix(2000)
        m.fs.unit.tube_heat_transfer_coefficient.fix(51000)

        m.fs.unit.shell_inlet.flow_mol[0].fix(5)  # mol/s
        m.fs.unit.shell_inlet.temperature[0].fix(365)  # K
        m.fs.unit.shell_inlet.pressure[0].fix(101325)  # Pa
        m.fs.unit.shell_inlet.mole_frac_comp[0, "benzene"].fix(0.5)
        m.fs.unit.shell_inlet.mole_frac_comp[0, "toluene"].fix(0.5)

        m.fs.unit.tube_inlet.flow_mol[0].fix(1)  # mol/s
        m.fs.unit.tube_inlet.temperature[0].fix(540)  # degR
        m.fs.unit.tube_inlet.pressure[0].fix(101.325)  # kPa
        m.fs.unit.tube_inlet.mole_frac_comp[0, "benzene"].fix(0.5)
        m.fs.unit.tube_inlet.mole_frac_comp[0, "toluene"].fix(0.5)

        return m

    @pytest.mark.component
    def test_build(self, btx):
        assert hasattr(btx.fs.unit, "shell_inlet")
        assert len(btx.fs.unit.shell_inlet.vars) == 4
        assert hasattr(btx.fs.unit.shell_inlet, "flow_mol")
        assert hasattr(btx.fs.unit.shell_inlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.shell_inlet, "temperature")
        assert hasattr(btx.fs.unit.shell_inlet, "pressure")

        assert hasattr(btx.fs.unit, "tube_inlet")
        assert len(btx.fs.unit.tube_inlet.vars) == 4
        assert hasattr(btx.fs.unit.tube_inlet, "flow_mol")
        assert hasattr(btx.fs.unit.tube_inlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.tube_inlet, "temperature")
        assert hasattr(btx.fs.unit.tube_inlet, "pressure")

        assert hasattr(btx.fs.unit, "shell_outlet")
        assert len(btx.fs.unit.shell_outlet.vars) == 4
        assert hasattr(btx.fs.unit.shell_outlet, "flow_mol")
        assert hasattr(btx.fs.unit.shell_outlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.shell_outlet, "temperature")
        assert hasattr(btx.fs.unit.shell_outlet, "pressure")

        assert hasattr(btx.fs.unit, "tube_outlet")
        assert len(btx.fs.unit.tube_outlet.vars) == 4
        assert hasattr(btx.fs.unit.tube_outlet, "flow_mol")
        assert hasattr(btx.fs.unit.tube_outlet, "mole_frac_comp")
        assert hasattr(btx.fs.unit.tube_outlet, "temperature")
        assert hasattr(btx.fs.unit.tube_outlet, "pressure")

        assert hasattr(btx.fs.unit, "shell_area")
        assert hasattr(btx.fs.unit, "shell_length")
        assert hasattr(btx.fs.unit, "tube_area")
        assert hasattr(btx.fs.unit, "tube_length")
        assert hasattr(btx.fs.unit, "d_shell")
        assert hasattr(btx.fs.unit, "d_tube_outer")
        assert hasattr(btx.fs.unit, "d_tube_inner")
        assert hasattr(btx.fs.unit, "N_tubes")
        assert hasattr(btx.fs.unit, "shell_heat_transfer_coefficient")
        assert hasattr(btx.fs.unit, "tube_heat_transfer_coefficient")
        assert hasattr(btx.fs.unit, "temperature_wall")
        assert hasattr(btx.fs.unit, "shell_heat_transfer_eq")
        assert hasattr(btx.fs.unit, "tube_heat_transfer_eq")
        assert hasattr(btx.fs.unit, "wall_0D_model")
        assert hasattr(btx.fs.unit, "area_calc_tube")
        assert hasattr(btx.fs.unit, "area_calc_shell")

        assert number_variables(btx) == 2021
        assert number_total_constraints(btx) == 1889
        assert number_unused_variables(btx) == 34

    @pytest.mark.integration
    def test_units(self, btx):
        assert_units_equivalent(btx.fs.unit.shell_area, pyunits.m**2)
        assert_units_equivalent(btx.fs.unit.shell_length, pyunits.m)
        assert_units_equivalent(btx.fs.unit.tube_area, pyunits.m**2)
        assert_units_equivalent(btx.fs.unit.tube_length, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_shell, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_tube_outer, pyunits.m)
        assert_units_equivalent(btx.fs.unit.d_tube_inner, pyunits.m)
        assert_units_equivalent(btx.fs.unit.N_tubes, pyunits.dimensionless)
        assert_units_equivalent(
            btx.fs.unit.shell_heat_transfer_coefficient,
            pyunits.W / pyunits.m**2 / pyunits.K,
        )
        assert_units_equivalent(
            btx.fs.unit.tube_heat_transfer_coefficient,
            pyunits.kW / pyunits.m**2 / pyunits.degR,
        )
        assert_units_equivalent(btx.fs.unit.temperature_wall, pyunits.K)

        assert_units_consistent(btx)

    @pytest.mark.component
    def test_dof(self, btx):
        assert degrees_of_freedom(btx) == 0

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_performance_contents(self, btx):
        perf_dict = btx.fs.unit._get_performance_contents()

        assert perf_dict == {
            "vars": {
                "Number of Tubes": btx.fs.unit.N_tubes,
                "Shell Area": btx.fs.unit.shell.area,
                "Shell Diameter": btx.fs.unit.d_shell,
                "Shell Length": btx.fs.unit.shell.length,
                "Tube Area": btx.fs.unit.tube.area,
                "Tube Inner Diameter": btx.fs.unit.d_tube_inner,
                "Tube Length": btx.fs.unit.tube.length,
                "Tube Outer Diameter": btx.fs.unit.d_tube_outer}}

    @pytest.mark.ui
    @pytest.mark.unit
    def test_get_stream_table_contents(self, btx):
        stable = btx.fs.unit._get_stream_table_contents()

        expected = {
            'Units': {
                'Total Molar Flowrate': getattr(pyunits.pint_registry, "mole/second"),
                'Total Mole Fraction benzene': getattr(pyunits.pint_registry, "dimensionless"),
                'Total Mole Fraction toluene': getattr(pyunits.pint_registry, "dimensionless"),
                'Temperature': getattr(pyunits.pint_registry, "kelvin"),
                'Pressure': getattr(pyunits.pint_registry, "Pa")},
            'Shell Inlet': {
                'Total Molar Flowrate': pytest.approx(5.0, rel=1e-4),
                'Total Mole Fraction benzene': pytest.approx(0.5, rel=1e-4),
                'Total Mole Fraction toluene': pytest.approx(0.5, rel=1e-4),
                'Temperature': pytest.approx(365, rel=1e-4),
                'Pressure': pytest.approx(101325.0, rel=1e-4)},
            'Shell Outlet': {
                'Total Molar Flowrate': pytest.approx(100.0, rel=1e-4),
                'Total Mole Fraction benzene': pytest.approx(0.5, rel=1e-4),
                'Total Mole Fraction toluene': pytest.approx(0.5, rel=1e-4),
                'Temperature': pytest.approx(300, rel=1e-4),
                'Pressure': pytest.approx(1e5, rel=1e-4)},
            'Tube Inlet': {
                'Total Molar Flowrate': pytest.approx(1.0, rel=1e-4),
                'Total Mole Fraction benzene': pytest.approx(0.5, rel=1e-4),
                'Total Mole Fraction toluene': pytest.approx(0.5, rel=1e-4),
                'Temperature': pytest.approx(300, rel=1e-4),
                'Pressure': pytest.approx(101325.0, rel=1e-4)},
            'Tube Outlet': {
                'Total Molar Flowrate': pytest.approx(100.0, rel=1e-4),
                'Total Mole Fraction benzene': pytest.approx(0.5, rel=1e-4),
                'Total Mole Fraction toluene': pytest.approx(0.5, rel=1e-4),
                'Temperature': pytest.approx(300, rel=1e-4),
                'Pressure': pytest.approx(1e5, rel=1e-4)}}

        assert stable.to_dict() == expected

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.integration
    def test_initialize(self, btx):
        initialization_tester(btx)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.integration
    def test_solve(self, btx):
        results = solver.solve(btx)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.integration
    def test_solution(self, btx):
        assert pytest.approx(5, abs=1e-3) == value(btx.fs.unit.shell_outlet.flow_mol[0])
        assert pytest.approx(322.959, abs=1e-3) == value(
            btx.fs.unit.shell_outlet.temperature[0]
        )
        assert pytest.approx(101325, abs=1e-3) == value(
            btx.fs.unit.shell_outlet.pressure[0]
        )

        assert pytest.approx(1, abs=1e-3) == value(btx.fs.unit.tube_outlet.flow_mol[0])
        assert pytest.approx(581.126, abs=1e-3) == value(
            btx.fs.unit.tube_outlet.temperature[0]
        )
        assert pytest.approx(101.325, abs=1e-3) == value(
            btx.fs.unit.tube_outlet.pressure[0]
        )

    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.integration
    def test_conservation(self, btx):
        assert (
            abs(
                value(
                    btx.fs.unit.shell_inlet.flow_mol[0]
                    - btx.fs.unit.shell_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )
        assert (
            abs(
                value(
                    btx.fs.unit.tube_inlet.flow_mol[0]
                    - btx.fs.unit.tube_outlet.flow_mol[0]
                )
            )
            <= 1e-6
        )

        shell_side = value(
            btx.fs.unit.shell_outlet.flow_mol[0]
            * (
                btx.fs.unit.shell.properties[0, 0].enth_mol_phase["Liq"]
                - btx.fs.unit.shell.properties[0, 1].enth_mol_phase["Liq"]
            )
        )
        tube_side = value(
            pyunits.convert(
                btx.fs.unit.tube_outlet.flow_mol[0]
                * btx.fs.unit.N_tubes
                * (
                    btx.fs.unit.tube.properties[0, 1].enth_mol_phase["Liq"]
                    - btx.fs.unit.tube.properties[0, 0].enth_mol_phase["Liq"]
                ),
                to_units=pyunits.W,
            )
        )
        assert abs((shell_side - tube_side) / shell_side) <= 1e-4

    @pytest.mark.component
    def test_initialization_error(self, btx):
        btx.fs.unit.shell_outlet.flow_mol[0].fix(20)

        with idaes.temporary_config_ctx():
            with pytest.raises(InitializationError):
                btx.fs.unit.initialize(optarg={"max_iter": 1})
