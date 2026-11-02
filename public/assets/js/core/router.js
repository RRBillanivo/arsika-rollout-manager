// Hash router: "#/path" -> route {pattern, page, access, menu, title, full}.
// access: guest (signed out only), all, auth (users and admins), user (non-admin users), admin.

export class Router {
  constructor(routes) {
    this.routes = routes;
  }

  /** Current path from the location hash ("/" when there is none). */
  currentPath() {
    return location.hash.startsWith('#/') ? location.hash.slice(1) : '/';
  }

  /** -> {route, params} or null */
  match(path) {
    const route = this.routes.find((r) => r.pattern.test(path));
    return route ? { route, params: path.match(route.pattern).slice(1) } : null;
  }

  /** May a visitor with this role ('guest', 'user', 'admin') open the route? */
  static allows(route, role) {
    switch (route.access) {
      case 'guest': return role === 'guest';
      case 'all': return true;
      case 'auth': return role !== 'guest';
      case 'user': return role === 'user';
      case 'admin': return role === 'admin';
      default: return false;
    }
  }

  go(path) {
    location.hash = '#' + path;
  }

  replace(path) {
    location.replace('#' + path);
  }
}
