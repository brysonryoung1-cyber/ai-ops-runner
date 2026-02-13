/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Ensure the server only binds to localhost
  // (also enforced via CLI flags in package.json scripts)
};

module.exports = nextConfig;
