# Codebase Patterns - AestheticcNext

> **Read this FIRST before writing any code.** These patterns were discovered during previous Ralph executions.

## Quality Gates

```bash
# USE THIS (project quality gate)
npm run lint -- --quiet

# DO NOT USE (doesn't exist)
npm run typecheck

# DO NOT USE (2900+ pre-existing errors - project skips type validation)
npx tsc --noEmit
```

## API Patterns (Pages Router)

APIs live in `pages/api/` (NOT App Router). Use this exact pattern:

```typescript
import { withApiWrapper } from "@/lib/middleware/api-wrapper";
import { withLegacyAuth } from "@/lib/middleware/auth-middleware";
import { Response } from "@/lib/api/responses";
import { AuthUtils } from "@/lib/api/auth-utils";
import { getUserBusinessId } from "@/lib/db/team-utils";
import { Logger } from "@/lib/utils/logger";

const handler = withLegacyAuth(async (req, res) => {
  const userId = AuthUtils.getUserId(req);
  const businessId = await getUserBusinessId(userId);

  switch (req.method) {
    case "GET":
      // ... handle GET
      return Response.success(res, { data });
    case "POST":
      // ... handle POST
      return Response.success(res, { data });
    default:
      return Response.methodNotAllowed(res);
  }
});

export default withApiWrapper(handler);
```

## Database (Drizzle ORM)

Schema file: `lib/schema.ts` (monolithic, ~2000+ lines)

```typescript
// Table pattern
export const myTable = pgTable("my_table", {
  id: text("id").primaryKey().$defaultFn(() => crypto.randomUUID()),
  businessId: text("business_id").notNull().references(() => businessProfiles.id),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

// Type inference (tables don't export types)
import { InferSelectModel } from "drizzle-orm";
type MyRecord = InferSelectModel<typeof schema.myTable>;
```

## Encryption (for OAuth tokens)

```typescript
import { encrypt, decrypt } from "@/lib/utils/encryption";

// Store encrypted
const encryptedToken = encrypt(accessToken);

// Read back
const accessToken = decrypt(encryptedToken);
```

Reference: `pages/api/twilio/configure.ts`

## Testing (Jest)

```bash
# Run specific tests
npm run test -- --testPathPattern=feature-name --passWithNoTests

# Tests go in __tests__/
# May need to add path to jest.config.js testMatch
```

Required packages for new test files:
- `jest-environment-jsdom`
- `setimmediate`

## UI Components

- Components in `components/` directory
- Use shadcn UI from `@/components/ui/`
- Use TanStack Query: `useQuery`, `useMutation`
- Check existing settings components for patterns

## Environment Variables

- `.env.local` - actual values (not committed)
- `.env.example` - documentation (committed)

## Multi-Tenancy

**ALL tables must have `businessId`** for data isolation. Use:
```typescript
const businessId = await getUserBusinessId(userId);
// Then filter all queries by businessId
```

---

*Last updated from Xero integration learnings (2026-02-02)*
