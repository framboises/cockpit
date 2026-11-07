// La coquille charge Field depuis le serveur : un seul code web, partage avec la PWA.
// FIELD_SERVER_URL permet de viser un serveur local pendant le developpement.
const url = process.env.FIELD_SERVER_URL || 'https://cockpit.lemans.org/field';

module.exports = {
  appId: 'org.lemans.cockpit.field',
  appName: 'Field',
  webDir: 'www',
  server: {
    url,
    cleartext: url.startsWith('http://'),
  },
  android: {
    allowMixedContent: false,
  },
};
