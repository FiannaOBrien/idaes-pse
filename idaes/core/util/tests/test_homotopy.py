##############################################################################
# Institute for the Design of Advanced Energy Systems Process Systems
# Engineering Framework (IDAES PSE Framework) Copyright (c) 2018-2019, by the
# software owners: The Regents of the University of California, through
# Lawrence Berkeley National Laboratory,  National Technology & Engineering
# Solutions of Sandia, LLC, Carnegie Mellon University, West Virginia
# University Research Corporation, et al. All rights reserved.
#
# Please see the files COPYRIGHT.txt and LICENSE.txt for full copyright and
# license information, respectively. Both files are also available online
# at the URL "https://github.com/IDAES/idaes-pse".
##############################################################################
"""
IDAES Homotopy meta-solver tests.
"""

__author__ = "Andrew Lee"

import pytest

from pyomo.environ import ConcreteModel, Constraint, Var, TerminationCondition

from idaes.core import FlowsheetBlock
from idaes.property_models.activity_coeff_models.BTX_activity_coeff_VLE \
    import BTXParameterBlock
from idaes.core.util.model_statistics import degrees_of_freedom

from idaes.core.util.homotopy import homotopy


@pytest.fixture()
def model():
    m = ConcreteModel()
    m.x = Var(initialize=1)
    m.y = Var(initialize=1)

    m.c = Constraint(expr=m.y == m.x**2)

    m.x.fix(10)

    return m


# -----------------------------------------------------------------------------
# Test argument validation


# -----------------------------------------------------------------------------
# Test termination conditions
def test_basic(model):
    tc, prog, ni = homotopy(model, [model.x], [20])

    assert model.y.value == 400

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 4


def test_basic_overshoot(model):
    # Use a big step such that overshoot will occur if not caught
    tc, prog, ni = homotopy(model, [model.x], [20], step_init=0.6)

    assert model.y.value == 400

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 2


def test_basic_constraint_violation(model):
    # Add a constraint to limit y
    model.c2 = Constraint(expr=model.y <= 300)

    # Try to walk to a value of x that gives an infeasible value of y
    tc, prog, ni = homotopy(model, [model.x], [20])

    assert pytest.approx(model.y.value, 1e-4) == 293.27

    assert tc == TerminationCondition.minStepLength
    assert prog == 0.7125
    assert ni == 12


def test_basic_max_iter(model):
    tc, prog, ni = homotopy(model, [model.x], [20], max_eval=2)

    assert pytest.approx(model.y.value, 1e-4) == 182.25

    assert tc == TerminationCondition.maxEvaluations
    assert prog == 0.35
    assert ni == 2


def test_basic_infeasible_init(model):
    model.c2 = Constraint(expr=model.y <= 50)

    tc, prog, ni = homotopy(model, [model.x], [20])

    assert tc == TerminationCondition.infeasible
    assert prog == 0
    assert ni == 0


# TODO : need tests for convergence with regularisation
# -----------------------------------------------------------------------------
# Test that parameters have correct effect
def test_basic_step_accel(model):
    # With zero acceleration, should take 10 steps
    tc, prog, ni = homotopy(model, [model.x], [20], step_accel=0)

    assert model.y.value == 400

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 10


def test_basic_step_init(model):
    # With zero acceleration and initial step of 0.05, should take 20 steps
    tc, prog, ni = homotopy(model, [model.x], [20],
                            step_init=0.05, step_accel=0)

    assert model.y.value == 400

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 20


def test_basic_step_cut(model):
    # Add a constraint to limit y
    model.c2 = Constraint(expr=model.y <= 196)

    # Should take 6 steps
    # 4 steps to reach 14, and 2 to cut back to min_step
    tc, prog, ni = homotopy(model, [model.x], [20], step_init=0.1,
                            min_step=0.025, step_cut=0.25, step_accel=0)

    assert model.y.value == 196

    assert tc == TerminationCondition.minStepLength
    assert prog == 0.4
    assert ni == 6


def test_basic_step_cut_2(model):
    # Add a constraint to limit y
    model.c2 = Constraint(expr=model.y <= 196)

    # Should take 7 steps
    # 4 steps to reach 14, and 3 to cut back to min_step
    tc, prog, ni = homotopy(model, [model.x], [20], step_init=0.1,
                            min_step=0.01, step_cut=0.25, step_accel=0)

    assert model.y.value == 196

    assert tc == TerminationCondition.minStepLength
    assert prog == 0.4
    assert ni == 7


def test_basic_iter_target(model):
    # Should take 5 steps
    tc, prog, ni = homotopy(model, [model.x], [20], iter_target=2)

    assert model.y.value == 400

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 5


def test_basic_max_step(model):
    # With max_step = step_init = 0.1, should take 10 steps
    tc, prog, ni = homotopy(model, [model.x], [20], max_step=0.1)

    assert model.y.value == 400

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 10


# -----------------------------------------------------------------------------
# Test a more complex problem
@pytest.fixture()
def model2():
    m = ConcreteModel()
    m.fs = FlowsheetBlock(default={"dynamic": False})

    # vapor-liquid (ideal) - FTPz
    m.fs.properties_ideal_vl_FTPz = BTXParameterBlock(
        default={"valid_phase": ('Liq', 'Vap'),
                 "activity_coeff_model": "Ideal",
                 "state_vars": "FTPz"})
    m.fs.state_block =\
        m.fs.properties_ideal_vl_FTPz.state_block_class(
                default={"parameters": m.fs.properties_ideal_vl_FTPz,
                         "defined_state": True})

    m.fs.state_block.flow_mol.fix(1)
    m.fs.state_block.temperature.fix(360)
    m.fs.state_block.pressure.fix(101325)
    m.fs.state_block.mole_frac_comp["benzene"].fix(0.5)
    m.fs.state_block.mole_frac_comp["toluene"].fix(0.5)

    assert degrees_of_freedom(m.fs.state_block) == 0

    m.fs.state_block.initialize()

    return m


def test_ideal_prop(model2):
    tc, prog, ni = homotopy(
            model2, [model2.fs.state_block.temperature], [390])

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 5

    # Check for VLE results
    assert model2.fs.state_block.mole_frac_phase_comp["Liq", "benzene"].value \
        == pytest.approx(0.291, abs=1e-3)
    assert model2.fs.state_block.mole_frac_phase_comp["Liq", "toluene"].value \
        == pytest.approx(0.709, abs=1e-3)
    assert model2.fs.state_block.mole_frac_phase_comp["Vap", "benzene"].value \
        == pytest.approx(0.5, abs=1e-5)
    assert model2.fs.state_block.mole_frac_phase_comp["Vap", "toluene"].value \
        == pytest.approx(0.5, abs=1e-5)


# Test max_iter here, as a more complicated model is needed
def test_ideal_prop_max_iter(model2):
    tc, prog, ni = homotopy(
            model2, [model2.fs.state_block.temperature], [390],
            max_solver_iterations=3, min_step=0.01, step_accel=0)

    assert tc == TerminationCondition.optimal
    assert prog == 1
    assert ni == 19

    # Check for VLE results
    assert model2.fs.state_block.mole_frac_phase_comp["Liq", "benzene"].value \
        == pytest.approx(0.291, abs=1e-3)
    assert model2.fs.state_block.mole_frac_phase_comp["Liq", "toluene"].value \
        == pytest.approx(0.709, abs=1e-3)
    assert model2.fs.state_block.mole_frac_phase_comp["Vap", "benzene"].value \
        == pytest.approx(0.5, abs=1e-5)
    assert model2.fs.state_block.mole_frac_phase_comp["Vap", "toluene"].value \
        == pytest.approx(0.5, abs=1e-5)
