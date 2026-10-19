"""Sign in or create an account.

Sign-in uses Streamlit's OIDC login (`st.login`) with the providers configured in Streamlit Secrets: Google,
Microsoft and an "email" provider (a hosted identity service such as Auth0 that offers email sign-up, verification and
password reset). Passwords are typed on the provider's own page; this app never sees or stores them. A new account
is created the first time someone signs in.
"""
import streamlit as st

from src.ui.brand import signin_brand_html
from src.ui.state import PROVIDERS, configured_providers, current_user, login

ICONS = {"google": ":material/account_circle:", "microsoft": ":material/window:", "email": ":material/mail:"}
user = current_user()

with st.container(key="card-signin"):
    if not user.is_guest:
        st.html(signin_brand_html("You're signed in", "Pick up where you left off."))
        st.page_link("app_pages/1_Research_Workspace.py", label="Open the research workspace", icon=":material/travel_explore:")
        st.stop()

    mode = st.segmented_control("Account", ["Sign in", "Create account"], default="Sign in", key="signin_mode",
                                label_visibility="collapsed", width="stretch") or "Sign in"
    creating = mode == "Create account"
    st.html(signin_brand_html("Create your account" if creating else "Welcome back",
                              "Keep a private library of your research." if creating
                              else "Sign in to continue your research."))

    providers = configured_providers()
    if not providers:
        st.info("Sign-in is not configured for this app.", icon=":material/info:")
    for name in providers:
        if name == "email":
            label = "Sign up with email" if creating else "Continue with email"
        else:
            label = f"{'Sign up' if creating else 'Continue'} with {PROVIDERS[name]}"
        st.button(label, key=f"login_{name}", icon=ICONS[name], width="stretch", on_click=login, args=(name,))
    email = "email" in providers
    if creating and providers:
        st.caption("Your account is created the first time you sign in."
                   + (" With email, you choose a password on our sign-in provider's secure page and confirm your "
                      "address there. This app never sees your password." if email else ""))
    elif email:
        st.caption("Email sign-in opens our sign-in provider's secure page, where you can also reset a forgotten "
                   "password.")
    st.divider()
    st.page_link("app_pages/1_Research_Workspace.py", label="Just exploring? Try the demo without an account",
                 icon=":material/science:")
