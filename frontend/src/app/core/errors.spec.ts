import { HttpErrorResponse } from '@angular/common/http';

import { errorMessage } from './errors';

describe('errorMessage', () => {
  it('names the field of a validation error', () => {
    const err = new HttpErrorResponse({
      status: 422,
      error: {
        detail: [
          { loc: ['body', 'monthly_queries'], msg: 'Input should be greater than or equal to 0' },
        ],
      },
    });
    expect(errorMessage(err)).toBe('Monthly queries: input should be greater than or equal to 0');
  });
});
