// Redirect old addresses to jiudesign.shop (301)
const MAIN_HOST = "jiudesign.shop";
const REDIRECT_HOSTS = ["jiudesign.gracia0304.workers.dev", "www.jiudesign.shop"];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (REDIRECT_HOSTS.includes(url.hostname)) {
      url.hostname = MAIN_HOST;
      url.protocol = "https:";
      url.port = "";
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
