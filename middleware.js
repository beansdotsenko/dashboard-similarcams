import { next } from '@vercel/edge'

export const config = {
  matcher: '/:path*',
}

export default function middleware(request) {
  const authHeader = request.headers.get('authorization')
  const expectedAuth = 'Basic ' + btoa('camsboard:Nx7$kQpR2w')

  if (authHeader === expectedAuth) {
    return next()
  }

  return new Response('Unauthorized', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Dashboard"',
    },
  })
}
