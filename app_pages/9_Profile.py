"""Update profile: optional details about the user and their research interests.

Everything is optional and private to the user: it is not shown to administrators, not sent to search providers or
the LLM, and "Delete my data" in the Research library removes it.
"""
import streamlit as st
from pydantic import ValidationError

from src.models.profile import (AGE_RANGES, EDUCATION, INDUSTRIES, INTEREST_SUGGESTIONS, MAX_INTERESTS, SEXES,
                                UserProfile)
from src.ui.components import card, page_header
from src.ui.state import current_user, get_repo

repo, user = get_repo(), current_user()
page_header("Account", "Update profile", "Tell us a little about yourself. Every field is optional and only you can "
            "see it.")
if user.is_guest:
    st.info("Sign in to create a profile.", icon=":material/info:")
    st.stop()

profile = repo.get_profile(user.id)


def index(options: list[str], value: str) -> int:
    return options.index(value) if value in options else 0


with card("profile"), st.form("profile_form", border=False):
    st.markdown("**About you**")
    display_name = st.text_input("Display name", value=profile.display_name or ("" if user.name in ("Local user",
                                 "Signed-in user") else user.name), max_chars=60, placeholder="How we greet you")
    with st.container(horizontal=True):
        age = st.selectbox("Age range", AGE_RANGES, index=index(AGE_RANGES, profile.age_range))
        sex = st.selectbox("Sex", SEXES, index=index(SEXES, profile.sex))
    st.markdown("**Work and study**")
    with st.container(horizontal=True):
        education = st.selectbox("Highest education", EDUCATION, index=index(EDUCATION, profile.education))
        industry = st.selectbox("Industry", INDUSTRIES, index=index(INDUSTRIES, profile.industry))
    role = st.text_input("Role or occupation", value=profile.role, max_chars=80,
                         placeholder="e.g. Product manager, PhD student")
    st.markdown("**Research interests**")
    interests = st.multiselect(
        "Topics you research", sorted(set(INTEREST_SUGGESTIONS) | set(profile.research_interests)),
        default=profile.research_interests, accept_new_options=True, max_selections=MAX_INTERESTS,
        placeholder="Pick topics or type your own", help=f"Up to {MAX_INTERESTS}. Type a topic and press Enter to add it.")
    saved = st.form_submit_button("Save profile", type="primary", icon=":material/check:")

if saved:
    try:
        updated = UserProfile(display_name=display_name, age_range=age, sex=sex, education=education,
                              industry=industry, role=role, research_interests=interests)
    except ValidationError as exc:
        for error in exc.errors():
            st.error(error["msg"].removeprefix("Value error, "))
    else:
        repo.save_profile(user.id, updated)
        repo.log_activity(user.id, "profile_updated")
        st.success("Profile saved.", icon=":material/check_circle:")

st.caption("Your profile is stored with your account so the app can greet you and, in future, tailor suggestions. "
           "It is never shared with administrators, search providers or the language model. Delete it at any time "
           "with **Delete my data** in the Research library.")
