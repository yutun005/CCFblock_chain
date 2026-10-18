"""Planning guardrails before real funds are escrowed."""
import copy
import pytest
from app import Plan, data_plan, prepare_plan, JobRequest, units


@pytest.mark.parametrize("mutation",["cycle","unknown_dependency","duplicate_key","over_budget"])
def test_configured_dag_rejects_invalid_dependency_and_money_terms(mutation):
    value=data_plan([]).model_dump(mode="json")
    if mutation=="cycle":value["tasks"][0]["dependencies"]=[value["tasks"][-1]["key"]]
    elif mutation=="unknown_dependency":value["tasks"][0]["dependencies"]=["missing"]
    elif mutation=="duplicate_key":value["tasks"][1]["key"]=value["tasks"][0]["key"]
    else:value["deposit"]="1"
    with pytest.raises(ValueError):Plan.model_validate(value)


def test_configured_plan_preview_refuses_changed_expected_hash():
    with pytest.raises(ValueError):
        prepare_plan(JobRequest(expectedPlanHash="0x"+"11"*32),[])


@pytest.mark.parametrize("value",[0.1,"0.0000001","NaN","-1"])
def test_money_cannot_silently_round_or_accept_float(value):
    with pytest.raises((ValueError,TypeError)):units(value)
