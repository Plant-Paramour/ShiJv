"""Resolve the workspace profile for local routes."""
def local_user(request):
    return request.app.state.users.local_profile()
